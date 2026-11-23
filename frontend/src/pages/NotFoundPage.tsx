import { Link } from 'react-router-dom';
import { MapPinOff } from 'lucide-react';
import { EmptyState } from '@/components/shared/EmptyState';

export function NotFoundPage({ home = '/' }: { home?: string }) {
  return (
    <EmptyState
      icon={<MapPinOff className="h-12 w-12" />}
      title="Page not found"
      description="This address doesn't match any page in GameVault."
      action={
        <Link
          to={home}
          className="px-4 py-2 bg-accent-primary text-white rounded-md text-sm font-medium hover:bg-accent-primary/90 transition-colors no-underline"
        >
          Go back
        </Link>
      }
    />
  );
}
