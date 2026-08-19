import type { ReactNode } from "react";
import { AlertTriangle } from "lucide-react";

interface Props {
  children?: ReactNode;
}

/**
 * Wrapper for legacy portfolio-like reports.
 * Displays a warning that metrics are NOT true account-level results.
 */
export function LegacyStockPredReport({ children }: Props) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 rounded-md border border-orange-300 bg-orange-50 p-3 text-sm text-orange-800">
        <AlertTriangle className="h-4 w-4 shrink-0" />
        <span>
          Legacy report (non-cohort). Metrics use merged portfolio semantics and do NOT represent
          true account-level returns. Displayed for historical reference only.
        </span>
      </div>
      {children}
    </div>
  );
}
