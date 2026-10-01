import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Icon } from "../../components/Icon";
const USER_CLAMP_CHARS = 1200;

export function ClampedUserText({ text }: { text: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (text.length <= USER_CLAMP_CHARS) return <>{text}</>;
  return (
    <>
      {open ? text : text.slice(0, USER_CLAMP_CHARS).trimEnd() + "…"}
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="block ml-auto mt-1.5 conversation-type-ui font-medium opacity-75 hover:opacity-100"
      >
        {open ? t("transcript.user_less") : t("transcript.user_more")}
      </button>
    </>
  );
}

// Hover affordances for a message bubble (FB-005): copy the raw text + the message's time.
// Lives in a ZERO-HEIGHT strip under the bubble (absolute, inside the transcript's 20px gap)
// so revealing it on group-hover never shifts the layout. `ts` is unix seconds — canonical
// messages carry it, pre-stamp history doesn't, so the time simply omits itself when absent.
export function BubbleMeta({
  text,
  ts,
  align,
}: {
  text: string;
  ts?: number;
  align: "left" | "right";
}) {
  const { t } = useTranslation();
  const [copied, setCopied] = useState(false);
  const copyTimer = useRef<ReturnType<typeof setTimeout>>();
  useEffect(() => () => clearTimeout(copyTimer.current), []);
  const when = typeof ts === "number" ? new Date(ts * 1000) : null;
  const copy = () => {
    // "Copied" only after the write actually lands — WebKit can reject outside a
    // trusted gesture, and claiming success on a silent no-op would gaslight the user.
    navigator.clipboard
      ?.writeText(text)
      .then(() => {
        setCopied(true);
        clearTimeout(copyTimer.current);
        copyTimer.current = setTimeout(() => setCopied(false), 1200);
      })
      .catch(() => {});
  };
  return (
    <div className="relative h-0 select-none">
      <div
        className={
          "absolute top-1 flex items-center gap-1.5 conversation-type-caption leading-none text-faint whitespace-nowrap opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity " +
          (align === "right" ? "right-0" : "left-0")
        }
      >
        <button
          className="flex items-center cursor-pointer hover:text-muted"
          data-testid="bubble-copy"
          title={t("transcript.copy_message")}
          onClick={copy}
        >
          {copied ? t("transcript.copied") : <Icon name="copy" size={11} />}
        </button>
        {when && (
          <span data-testid="bubble-ts" title={when.toLocaleString()}>
            {when.toLocaleTimeString([], {
              hour: "numeric",
              minute: "2-digit",
            })}
          </span>
        )}
      </div>
    </div>
  );
}
