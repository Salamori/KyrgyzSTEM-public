"use client";

import Link from "next/link";
import { useState } from "react";

import { api, unwrap } from "@/lib/api/client";
import { clearDrafts, draftKeys } from "@/lib/drafts";
import { useI18n } from "@/lib/i18n/context";

import { errorKey } from "./errorMessage";
import { ErrorState, SuccessMessage } from "./states";

/** What is stored and who can see it, plus control over this device's drafts and a data export. */
export function PrivacyPanel() {
  const { t } = useI18n();
  const [count, setCount] = useState(() => (typeof window === "undefined" ? 0 : draftKeys().length));
  const [cleared, setCleared] = useState(false);
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState<unknown>(null);

  function onClearDrafts() {
    clearDrafts();
    setCount(draftKeys().length);
    setCleared(true);
  }

  async function onExport() {
    setExporting(true);
    setExportError(null);
    try {
      const data = await unwrap(api.GET("/api/v1/users/me/export"));
      const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
      const link = document.createElement("a");
      link.href = url;
      link.download = "kyrgyzstem-my-data.json";
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setExportError(err);
    } finally {
      setExporting(false);
    }
  }

  return (
    <section aria-labelledby="privacy-title" className="card flex flex-col gap-3">
      <h2 id="privacy-title" className="text-xl font-semibold">
        {t("privacy.title")}
      </h2>
      <p className="text-slate-700">{t("privacy.stored")}</p>
      <p className="text-slate-700">{t("privacy.visibility")}</p>
      <p className="text-slate-700">{t("privacy.drafts", { n: count })}</p>
      {cleared && <SuccessMessage>{t("privacy.cleared")}</SuccessMessage>}
      <div>
        <button type="button" className="btn-secondary" disabled={count === 0} onClick={onClearDrafts}>
          {t("privacy.clearDrafts")}
        </button>
      </div>
      <p className="text-slate-700">{t("privacy.exportBody")}</p>
      {exportError !== null && <ErrorState message={t(errorKey(exportError))} />}
      <div>
        <button type="button" className="btn-secondary" disabled={exporting} onClick={() => void onExport()}>
          {exporting ? t("privacy.exporting") : t("privacy.export")}
        </button>
      </div>
      <p className="text-sm text-slate-600">
        <Link href="/privacy">{t("nav.privacyPolicy")}</Link>
      </p>
    </section>
  );
}
