import { memo } from "react";
import { ConversationTranscript } from "./ConversationTranscript";

/** Tokens are subscribed only by the last product turn inside the transcript. */
export const ConversationView = memo(ConversationTranscript);
