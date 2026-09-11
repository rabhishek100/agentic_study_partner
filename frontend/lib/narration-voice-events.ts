export const NARRATION_VOICE_QUESTION = "mugensei:narration-voice-question";
export const NARRATION_VOICE_ANSWER = "mugensei:narration-voice-answer";

export interface NarrationVoiceQuestionDetail {
  requestId: string;
  conversationId: string;
  parentTurnIndex: number;
  quotedText: string;
  question: string;
}

export interface NarrationVoiceAnswerDetail {
  requestId: string;
  sideChatId: string;
  turnIndex?: number;
  error?: string;
}

export function emitNarrationVoiceQuestion(detail: NarrationVoiceQuestionDetail): void {
  window.dispatchEvent(new CustomEvent(NARRATION_VOICE_QUESTION, { detail }));
}

export function emitNarrationVoiceAnswer(detail: NarrationVoiceAnswerDetail): void {
  window.dispatchEvent(new CustomEvent(NARRATION_VOICE_ANSWER, { detail }));
}
