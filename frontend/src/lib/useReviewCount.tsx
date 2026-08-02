/**
 * How many facts still need checking.
 *
 * Shared rather than fetched twice: the sidebar shows the number and the review
 * screen changes it, and those have to be the same number. Deciding an item
 * calls `refresh()`, so the badge drops as the queue empties instead of going
 * stale until the next navigation.
 *
 * The count comes from `GET /timeline/review`, which returns totals alongside
 * the items — so this is one small query, not a second source of truth.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { useAuth } from "./useAuth";
import { getReviewQueue } from "./timeline";

interface ReviewCountValue {
  total: number;
  highRisk: number;
  refresh: () => Promise<void>;
}

const ReviewCountContext = createContext<ReviewCountValue | null>(null);

export function ReviewCountProvider({ children }: { children: ReactNode }) {
  const { getToken } = useAuth();
  const [total, setTotal] = useState(0);
  const [highRisk, setHighRisk] = useState(0);

  const refresh = useCallback(async () => {
    try {
      const queue = await getReviewQueue(await getToken());
      setTotal(queue.total);
      setHighRisk(queue.high_risk);
    } catch {
      // A badge is not worth an error state. The review screen reports the
      // problem properly when the user goes looking for it.
    }
  }, [getToken]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo(() => ({ total, highRisk, refresh }), [total, highRisk, refresh]);

  return <ReviewCountContext.Provider value={value}>{children}</ReviewCountContext.Provider>;
}

export function useReviewCount(): ReviewCountValue {
  const value = useContext(ReviewCountContext);
  if (!value) throw new Error("useReviewCount must be used inside <ReviewCountProvider>");
  return value;
}
