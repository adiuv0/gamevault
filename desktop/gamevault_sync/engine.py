"""One sync pass: scan, find what's new, upload it."""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import sources
from .api import ApiError, GameMissingError, GameVaultClient, ServerUnreachableError, SignedOutError
from .config import Settings
from .sources import GameRef, LocalFile
from .state import REJECTED, SYNCED, SyncState

log = logging.getLogger(__name__)

# A file modified this recently may still be being written by the game.
SETTLE_SECONDS = 3.0
HASH_BATCH = 500
UPLOAD_WORKERS = 2
# Resolving a new Steam game makes the server ask the Steam store for its name.
STEAM_LOOKUP_DELAY = 0.3


@dataclass
class Progress:
    phase: str  # scanning | reading | checking | uploading
    done: int = 0
    total: int = 0
    current: str = ""


@dataclass
class GameSummary:
    key: str
    name: str
    source: str
    found: int = 0
    new: int = 0
    excluded: bool = False


@dataclass
class SyncReport:
    dry_run: bool = False
    found: int = 0
    already: int = 0
    new: int = 0
    uploaded: int = 0
    duplicates: int = 0
    rejected: int = 0
    failed: int = 0
    waiting: int = 0  # still being written, or locked; picked up next time
    deferred: int = 0  # failed before; its next try isn't due yet
    cancelled: bool = False
    problems: list[str] = field(default_factory=list)
    games: list[GameSummary] = field(default_factory=list)
    uploaded_by_game: Counter = field(default_factory=Counter)
    steam_root: str | None = None
    specialk_root: str | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    @property
    def remaining(self) -> int:
        return max(0, self.new - self.uploaded - self.duplicates - self.rejected)


def sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        if hasattr(hashlib, "file_digest"):
            return hashlib.file_digest(fh, "sha256").hexdigest()
        digest = hashlib.sha256()
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
        return digest.hexdigest()


class Cancelled(Exception):
    pass


class SyncEngine:
    def __init__(
        self,
        client: GameVaultClient,
        state: SyncState,
        settings: Settings,
        *,
        on_progress: Callable[[Progress], None] | None = None,
        on_file: Callable[[str, str, str, str | None], None] | None = None,
        cancel: threading.Event | None = None,
        workers: int = UPLOAD_WORKERS,
        steam_lookup_delay: float = STEAM_LOOKUP_DELAY,
    ):
        self.client = client
        self.state = state
        self.settings = settings
        self._on_progress = on_progress or (lambda p: None)
        self._on_file = on_file or (lambda *a: None)
        self._cancel = cancel or threading.Event()
        self._workers = max(1, workers)
        self._steam_delay = steam_lookup_delay
        self._halt = threading.Event()
        self._fatal: BaseException | None = None
        self._resolve_lock = threading.Lock()
        self._count_lock = threading.Lock()

    # ── Public ───────────────────────────────────────────────────────────────

    def run(self, dry_run: bool = False) -> SyncReport:
        report = SyncReport(dry_run=dry_run)
        # Kept so a caller can see what got done before an error ended the pass.
        self.report = report
        try:
            self._run(report)
        except Cancelled:
            report.cancelled = True
        finally:
            report.finished_at = time.time()
        return report

    # ── Steps ────────────────────────────────────────────────────────────────

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise Cancelled()

    def _run(self, report: SyncReport) -> None:
        s = self.settings
        self._on_progress(Progress("scanning"))
        scan = sources.scan(s.steam_enabled, s.steam_path, s.specialk_enabled, s.specialk_path,
                            known_names=self.state.known_names())
        report.problems.extend(scan.problems)
        report.steam_root = str(scan.steam_root) if scan.steam_root else None
        report.specialk_root = str(scan.specialk_root) if scan.specialk_root else None

        excluded = set(s.excluded_games)
        summaries = {
            key: GameSummary(key, game.display_name, game.source, excluded=key in excluded)
            for key, game in scan.games.items()
        }
        for item in scan.files:
            summaries[item.game.key].found += 1
        report.games = sorted(summaries.values(), key=lambda g: g.name.lower())
        files = [f for f in scan.files if f.game.key not in excluded]
        report.found = len(files)
        self._check_cancel()

        candidates = self._hash_new_files(files, report)
        self._check_cancel()

        pending = self._ask_server(candidates, report)
        # The same picture saved in two places only needs to go up once.
        unique: dict[str, tuple[LocalFile, list[str]]] = {}
        for item, sha in pending:
            if sha in unique:
                unique[sha][1].append(item.path)
            else:
                unique[sha] = (item, [])
        report.new = len(unique)
        for item, _ in unique.values():
            summaries[item.game.key].new += 1
        if report.dry_run or not unique:
            return

        self._check_cancel()
        jobs = sorted(unique.items(), key=lambda kv: kv[1][0].mtime_ns, reverse=True)
        game_ids = self._resolve_games({item.game for item, _ in unique.values()}, report)
        self._upload_all(jobs, game_ids, report)

    def _hash_new_files(self, files: list[LocalFile], report: SyncReport) -> list[tuple[LocalFile, str]]:
        known = self.state.lookup([f.path for f in files])
        settle_ns = int(SETTLE_SECONDS * 1e9)
        now = time.time()
        now_ns = time.time_ns()
        candidates: list[tuple[LocalFile, str]] = []
        to_hash: list[LocalFile] = []
        for item in files:
            record = known.get(item.path)
            if record and record.size == item.size and record.mtime_ns == item.mtime_ns:
                if record.status == SYNCED:
                    report.already += 1
                    continue
                if record.status == REJECTED:
                    report.rejected += 1
                    continue
                if record.retry_at and record.retry_at > now:
                    report.deferred += 1
                    continue
                if record.sha256:
                    candidates.append((item, record.sha256))
                    continue
            # A timestamp in the future (a clock that was wrong, a copied file)
            # doesn't mean the game is still writing it.
            if 0 <= now_ns - item.mtime_ns < settle_ns:
                report.waiting += 1
                continue
            to_hash.append(item)

        batch: list[tuple[str, int, int, str]] = []
        for index, item in enumerate(to_hash):
            if index % 25 == 0:
                self._check_cancel()
                self._on_progress(Progress("reading", index, len(to_hash), item.filename))
            try:
                sha = sha256_file(item.path)
            except OSError as exc:
                log.info("can't read %s yet: %s", item.path, exc)
                report.waiting += 1
                continue
            batch.append((item.path, item.size, item.mtime_ns, sha))
            candidates.append((item, sha))
            if len(batch) >= 200:
                self.state.remember_hashes(batch)
                batch = []
        self.state.remember_hashes(batch)
        if to_hash:
            self._on_progress(Progress("reading", len(to_hash), len(to_hash)))
        return candidates

    def _ask_server(self, candidates: list[tuple[LocalFile, str]], report: SyncReport) -> list[tuple[LocalFile, str]]:
        hashes = sorted({sha for _, sha in candidates})
        existing: set[str] = set()
        for start in range(0, len(hashes), HASH_BATCH):
            self._check_cancel()
            self._on_progress(Progress("checking", start, len(hashes)))
            existing |= self.client.existing_hashes(hashes[start:start + HASH_BATCH])
        on_server = [item.path for item, sha in candidates if sha in existing]
        self.state.mark(on_server, SYNCED, "Already in GameVault")
        report.already += len(on_server)
        return [(item, sha) for item, sha in candidates if sha not in existing]

    def _resolve_game(self, game: GameRef, *, fresh: bool = False) -> int:
        if not fresh:
            server_id, _ = self.state.game(game.key)
            if server_id:
                return server_id
        if game.steam_app_id is not None:
            body = self.client.game_by_steam_app_id(game.steam_app_id, game.name or None)
            if self._steam_delay:
                time.sleep(self._steam_delay)
        else:
            body = self.client.game_by_name(game.display_name)
        self.state.remember_game(game.key, int(body["id"]), body.get("name") or game.display_name)
        return int(body["id"])

    def _resolve_games(self, games: set[GameRef], report: SyncReport) -> dict[str, int]:
        ids: dict[str, int] = {}
        ordered = sorted(games, key=lambda g: g.display_name.lower())
        for index, game in enumerate(ordered):
            self._check_cancel()
            self._on_progress(Progress("uploading", 0, report.new, f"Finding {game.display_name} in GameVault"))
            try:
                ids[game.key] = self._resolve_game(game)
            except (SignedOutError, ServerUnreachableError):
                raise
            except ApiError as exc:
                log.warning("couldn't find or create %s on the server: %s", game.display_name, exc)
                report.problems.append(f"Couldn't add {game.display_name} to GameVault: {exc}")
        return ids

    def _upload_all(self, jobs, game_ids: dict[str, int], report: SyncReport) -> None:
        total = len(jobs)
        done = [0]

        def work(job) -> None:
            _sha, (item, also_at) = job
            if self._halt.is_set() or self._cancel.is_set():
                return
            game_id = game_ids.get(item.game.key)
            if game_id is None:
                self.state.mark_failed([item.path, *also_at], "Couldn't add the game to GameVault")
                with self._count_lock:
                    report.failed += 1
                return
            name = self._game_name(item.game)
            try:
                try:
                    outcome = self.client.upload(game_id, item.path, taken_at_hint=item.taken_at_hint)
                except GameMissingError:
                    # Deleted or merged on the server since we last looked.
                    with self._resolve_lock:
                        game_id = self._resolve_game(item.game, fresh=True)
                        game_ids[item.game.key] = game_id
                    outcome = self.client.upload(game_id, item.path, taken_at_hint=item.taken_at_hint)
            except (SignedOutError, ServerUnreachableError) as exc:
                self._fatal = self._fatal or exc
                self._halt.set()
                return
            except OSError as exc:
                log.info("can't read %s: %s", item.path, exc)
                with self._count_lock:
                    report.waiting += 1
                return
            except ApiError as exc:
                log.warning("upload of %s failed: %s", item.path, exc)
                self.state.mark_failed([item.path, *also_at], str(exc))
                with self._count_lock:
                    report.failed += 1
                return

            paths = [item.path, *also_at]
            if outcome.outcome == "rejected":
                self.state.mark(paths, REJECTED, outcome.detail)
            else:
                self.state.mark(paths, SYNCED, outcome.outcome)
            if outcome.outcome != "duplicate":
                self.state.add_history(name, item.filename, outcome.outcome, outcome.detail)
            with self._count_lock:
                if outcome.outcome == "uploaded":
                    report.uploaded += 1
                    report.uploaded_by_game[name] += 1
                elif outcome.outcome == "duplicate":
                    report.duplicates += 1
                else:
                    report.rejected += 1
                done[0] += 1
                progress = Progress("uploading", done[0], total, f"{name} · {item.filename}")
            self._on_file(name, item.filename, outcome.outcome, outcome.detail)
            self._on_progress(progress)

        self._on_progress(Progress("uploading", 0, total))
        with ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix="upload") as pool:
            list(pool.map(work, jobs))
        if self._fatal is not None:
            raise self._fatal
        self._check_cancel()

    def _game_name(self, game: GameRef) -> str:
        if game.name:
            return game.name
        _, name = self.state.game(game.key)
        return name or game.display_name
