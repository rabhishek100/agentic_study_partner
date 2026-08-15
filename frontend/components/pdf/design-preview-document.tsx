"use client";

import { ChevronLeft, ChevronRight, Minus, Plus, Search } from "lucide-react";

import { Button } from "@/components/ui/button";

/**
 * Development-only visual fixture for Books design QA. Production sessions use
 * PdfViewer and authenticated source bytes; this keeps the exact study state
 * reproducible when a local backend has no uploaded books.
 */
export function DesignPreviewDocument({
  page,
  onPageChange,
}: {
  page: number;
  onPageChange: (page: number) => void;
}) {
  return (
    <div className="flex h-full min-h-0 flex-1 flex-col overflow-hidden bg-[#171a18]">
      <div className="flex h-12 shrink-0 items-center gap-2 border-b border-white/10 bg-[#202321] px-3 text-[#f2eee4]">
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Previous page"
          onClick={() => onPageChange(Math.max(1, page - 1))}
        >
          <ChevronLeft aria-hidden />
        </Button>
        <span className="rounded-md border border-white/10 bg-black/20 px-3 py-1 font-mono text-xs">{page}</span>
        <span className="text-xs text-white/55">/ 232</span>
        <span className="mx-2 h-5 w-px bg-white/10" aria-hidden />
        <Button size="icon-sm" variant="ghost" aria-label="Zoom out"><Minus aria-hidden /></Button>
        <span className="text-xs tabular-nums">100%</span>
        <Button size="icon-sm" variant="ghost" aria-label="Zoom in"><Plus aria-hidden /></Button>
        <Button size="icon-sm" variant="ghost" className="ml-auto" aria-label="Search document"><Search aria-hidden /></Button>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Next page"
          onClick={() => onPageChange(Math.min(232, page + 1))}
        >
          <ChevronRight aria-hidden />
        </Button>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4 sm:p-5">
        <article className="mx-auto min-h-[820px] w-full max-w-[590px] bg-[#f7f0df] px-[9%] py-[6%] font-serif text-[#241f18] shadow-xl">
          <header className="flex items-center gap-8 text-[0.64rem] font-semibold tracking-[0.08em]">
            <span className="text-sm">{page}</span>
            <span>CHAPTER 4</span>
            <span>DATA DISTRIBUTION SHIFTS</span>
          </header>
          <div className="mt-8 space-y-4 text-[0.93rem] leading-[1.55]">
            <p>
              Test data can be drastically different from training data. In deep learning,
              it is common to test on a distribution intentionally different from the one
              used to train a model so that we can measure how well it generalizes.
            </p>
            <p>Our goal is to avoid surprise, which comes from training-serving skew.</p>
            <p className="-mx-2 rounded bg-[#b8d4b3]/75 px-2 py-1.5">
              Training-serving skew occurs when the data that the model sees in production,
              which we call serving data, is different from the training data used to train
              the model. Serving data can differ in the input, feature, or label distribution.
              <sup className="ml-1 rounded bg-[#2f6d4f] px-1 font-sans text-[0.6rem] text-white">1</sup>
            </p>
            <p>
              Serving data can also differ from training data in ways that we cannot observe.
              For example, human annotators may label training data while end users produce
              the labels observed in production.
            </p>
            <p>
              Distribution shifts can happen unexpectedly. The only way to know that there
              is a shift is to continuously monitor the data and model performance.
            </p>
            <p>
              Depending on the nature of the shift, retraining may be sufficient. In other
              cases, we may need to update features, architecture, or the labeling strategy.
              <sup className="ml-1 text-[#2f6d4f]">2</sup>
            </p>
          </div>
          <footer className="mt-20 border-t border-[#42392d]/50 pt-3 text-[0.65rem] leading-relaxed">
            1. Training-serving skew is also known as dataset shift, data shift,
            training-test skew, or training-inference skew.
          </footer>
        </article>
      </div>
    </div>
  );
}
