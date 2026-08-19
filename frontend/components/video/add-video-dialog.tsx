"use client";

import { Plus } from "lucide-react";
import { useState } from "react";

import { AddVideo } from "@/components/video/add-video";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";

/**
 * Adding a lecture, as a task rather than as furniture.
 *
 * The form used to hold the rail permanently: four fields and a button, on
 * every visit, for something done once per lecture and then never again. It is
 * the same form — same two-phase upload, same slides-attached-up-front
 * ordering — summoned when it is wanted, where it also gets more width than
 * the rail ever gave it.
 */
export function AddVideoDialog({ onAdded }: { onAdded(): void }) {
  const [open, setOpen] = useState(false);

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button className="w-full justify-start">
          <Plus aria-hidden />
          Add lecture
        </Button>
      </DialogTrigger>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Add a lecture</DialogTitle>
          <DialogDescription>
            Paste a YouTube link or upload a file. Slides are optional, and
            attaching them now means the first answers can already cite them.
          </DialogDescription>
        </DialogHeader>
        <AddVideo
          onAdded={() => {
            // Closing here rather than inside the form: the form's job ends
            // when the lecture exists, and it is also used where there is no
            // dialog to close.
            setOpen(false);
            onAdded();
          }}
        />
      </DialogContent>
    </Dialog>
  );
}
