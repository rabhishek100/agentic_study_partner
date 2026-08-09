import type { InterviewStatus } from "./interview-types";
import type { InterviewVoiceStatus } from "@/hooks/use-interview-voice";

export type InterviewOperation =
  | "idle"
  | "loading_session"
  | "submitting_answer"
  | "checking_submission"
  | "asking_clarification"
  | "screen_checkpoint"
  | "pausing"
  | "resuming"
  | "finishing"
  | "loading_report";

export interface InterviewActivity {
  tone: "working" | "live" | "ready" | "paused";
  title: string;
  detail: string;
  stages?: string[];
}

export interface InterviewActivityInput {
  operation: InterviewOperation;
  interviewStatus: InterviewStatus;
  transitionInProgress: boolean;
  speechLoading: boolean;
  speechSpeaking: boolean;
  voiceStatus: InterviewVoiceStatus;
  listeningPaused: boolean;
  hasCurrentQuestion: boolean;
}

/** One honest, candidate-facing description of the work currently happening. */
export function describeInterviewActivity({
  operation,
  interviewStatus,
  transitionInProgress,
  speechLoading,
  speechSpeaking,
  voiceStatus,
  listeningPaused,
  hasCurrentQuestion,
}: InterviewActivityInput): InterviewActivity {
  switch (operation) {
    case "loading_session":
      return {
        tone: "working",
        title: "Restoring your interview",
        detail: "Loading the saved question, transcript, timer, and media state.",
      };
    case "submitting_answer":
      return {
        tone: "working",
        title: "Evaluating your answer",
        detail:
          "The server is comparing your response with the selected source, scoring it, and deciding whether to follow up or advance.",
        stages: ["Source grounding", "Answer evaluation", "Next-turn planning"],
      };
    case "checking_submission":
      return {
        tone: "working",
        title: "Confirming whether your answer was saved",
        detail:
          "The submission response failed, so the app is reloading this turn before asking you to retry.",
        stages: ["Reload session", "Match answered turn", "Restore next question"],
      };
    case "asking_clarification":
      return {
        tone: "working",
        title: "Clarifying the interview question",
        detail:
          "The interviewer is resolving the wording or requested response format without evaluating your answer or giving away the solution.",
        stages: ["Interpret your question", "Check source context", "Clarify the task"],
      };
    case "screen_checkpoint":
      return {
        tone: "working",
        title: "Analyzing your screen checkpoint",
        detail:
          "Capturing one still, checking the visible diagram, derivation, assumptions, or code, and attaching structured feedback to this answer.",
      };
    case "pausing":
      return {
        tone: "working",
        title: "Pausing the interview",
        detail: "Saving elapsed time and the current unanswered turn.",
      };
    case "resuming":
      return {
        tone: "working",
        title: "Resuming the interview",
        detail: "Restoring the current question, voice narration, and microphone capture.",
      };
    case "finishing":
      return {
        tone: "working",
        title: "Finishing the interview",
        detail: "Closing the active turn and calculating your final session metrics.",
      };
    case "loading_report":
      return {
        tone: "working",
        title: "Preparing your interview report",
        detail: "Loading scores, grounded feedback, revision topics, and citations.",
      };
  }

  if (transitionInProgress) {
    if (speechLoading) {
      return {
        tone: "working",
        title: "Preparing spoken feedback",
        detail: "Generating the interviewer's voice response to your answer.",
      };
    }
    if (speechSpeaking) {
      return {
        tone: "live",
        title: "The interviewer is responding",
        detail: "The next question will appear after this feedback finishes.",
      };
    }
    return {
      tone: "working",
      title: "Preparing the next turn",
      detail: "Your answer is saved. The interviewer response is about to play.",
    };
  }

  if (speechLoading) {
    return {
      tone: "working",
      title: "Preparing question audio",
      detail: "Generating hosted interviewer speech; device voice is the fallback.",
    };
  }
  if (speechSpeaking) {
    return {
      tone: "live",
      title: "The interviewer is asking the question",
      detail: "Microphone capture will start automatically when narration finishes.",
    };
  }
  if (voiceStatus === "starting") {
    return {
      tone: "working",
      title: "Connecting your microphone",
      detail: "Opening the selected input and calibrating to the room noise level.",
    };
  }
  if (voiceStatus === "processing") {
    return {
      tone: "working",
      title: "Transcribing your latest speech",
      detail: "Whisper is converting the completed audio segment and will append it to your draft.",
    };
  }
  if (voiceStatus === "recording") {
    return {
      tone: "live",
      title: "Capturing your answer",
      detail: "Keep speaking naturally. A pause ends only this segment and never submits the answer.",
    };
  }
  if (voiceStatus === "listening") {
    return {
      tone: "ready",
      title: "Listening for your answer",
      detail: "Speech is detected automatically; you still choose when to send the complete draft.",
    };
  }
  if (interviewStatus === "paused") {
    return {
      tone: "paused",
      title: "Interview paused",
      detail: "The timer and microphone are stopped. Resume when you are ready.",
    };
  }
  if (listeningPaused) {
    return {
      tone: "paused",
      title: "Microphone listening paused",
      detail: "Your draft is safe. Start listening again or continue typing.",
    };
  }
  if (interviewStatus === "active" && hasCurrentQuestion) {
    return {
      tone: "ready",
      title: "Ready for your answer",
      detail: "Type your response or start listening; nothing is submitted automatically.",
    };
  }
  return {
    tone: "ready",
    title: "Interview ready",
    detail: "No background processing is currently running.",
  };
}
