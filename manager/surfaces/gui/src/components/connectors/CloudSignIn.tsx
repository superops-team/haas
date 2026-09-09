import { useTranslation } from "react-i18next";

// OpenHarness is local-only: cloud-account sign-in is intentionally not offered.
// Existing callers render this as a disabled explanation beside manual setup.
export function CloudSignInInline({ blurb }: { blurb?: string }) {
  const { t } = useTranslation();
  return (
    <div className="space-y-1.5">
      <button
        className="w-full px-3 py-2 rounded-lg border border-line text-faint text-[13px] font-medium"
        data-testid="inline-cloud-sign-in"
        disabled
      >
        {t("cloud.sign_in")}
      </button>
      <div className="text-[12px] text-faint">
        {blurb || t("modal.switch_to_manual")}
      </div>
    </div>
  );
}

// The UNKNOWN state: the status fetch hasn't resolved (or is being retried).
// Rendering the sign-in prompt here told signed-in users they weren't (FB-013) —
// pending must look like pending.
export function CloudStatusPending() {
  const { t } = useTranslation();
  return (
    <div
      className="text-[12px] text-faint py-2 text-center"
      data-testid="cloud-status-pending"
    >
      {t("cloud.checking")}
    </div>
  );
}
