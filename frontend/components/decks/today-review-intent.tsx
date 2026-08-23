"use client";

import { useSearchParams } from "next/navigation";
import { Suspense, useEffect } from "react";

function SearchParamIntent({ onRequested }: { onRequested: () => void }) {
  const searchParams = useSearchParams();
  const review = searchParams.get("review");

  useEffect(() => {
    if (review === "today") onRequested();
  }, [onRequested, review]);

  return null;
}

/** Opens Today directly while keeping the review queue owned by the Cards page. */
export function TodayReviewIntent({ onRequested }: { onRequested: () => void }) {
  return (
    <Suspense fallback={null}>
      <SearchParamIntent onRequested={onRequested} />
    </Suspense>
  );
}
