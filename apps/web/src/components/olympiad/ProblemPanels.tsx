"use client";

import Link from "next/link";
import { useEffect, useId, useState, type FormEvent } from "react";

import {
  api,
  ApiError,
  unwrap,
  type EditorialOut,
  type HintsOut,
  type Language,
  type ProblemDetail,
  type SubmissionDetail,
} from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";
import { isPending, useSubmissionStatus } from "@/lib/useSubmissionStatus";

import { errorKey } from "../errorMessage";
import { Markdown } from "../Markdown";
import { ErrorState, Loading, SuccessMessage } from "../states";
import { CheckingStatus, Outcome } from "./Submissions";

function Panel({ id, title, children }: { id: string; title: string; children: React.ReactNode }) {
  return (
    <section aria-labelledby={id} className="card flex flex-col gap-3">
      <h2 id={id} className="text-lg font-semibold">
        {title}
      </h2>
      {children}
    </section>
  );
}

function SignInPrompt({ text }: { text: string }) {
  return (
    <p>
      <Link href="/login">{text}</Link>
    </p>
  );
}

// ------------------------------------------------------------------ hints

export function HintsPanel({ slug, total }: { slug: string; total: number }) {
  const { t } = useI18n();
  const { state } = useSession();
  return (
    <Panel id="hints-title" title={t("hints.title")}>
      {total === 0 ? (
        <p className="text-slate-600">{t("hints.none")}</p>
      ) : state.status !== "authenticated" ? (
        <SignInPrompt text={t("hints.signIn")} />
      ) : (
        <HintList slug={slug} />
      )}
    </Panel>
  );
}

function HintList({ slug }: { slug: string }) {
  const { t, locale } = useI18n();
  const hints = useLoad<HintsOut>(
    () => unwrap(api.GET("/api/v1/problems/{slug}/hints", { params: { path: { slug }, query: { locale } } })),
    [slug, locale],
  );
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  if (hints.state.status === "loading") return <Loading />;
  if (hints.state.status === "error") {
    return <ErrorState message={t(errorKey(hints.state.error))} onRetry={() => void hints.reload()} />;
  }
  const data = hints.state.data;
  const next = data.revealed.length + 1;

  async function reveal() {
    setPending(true);
    setError(null);
    try {
      hints.setData(
        await unwrap(
          api.POST("/api/v1/problems/{slug}/hints/{ordinal}/reveal", {
            params: { path: { slug, ordinal: next }, query: { locale } },
          }),
        ),
      );
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <p className="text-sm text-slate-600">{t("hints.recorded")}</p>
      {data.revealed.length > 0 && (
        <ol className="flex flex-col gap-2">
          {data.revealed.map((h) => (
            <li key={h.ordinal} className="rounded-md bg-sky-50 p-3" lang={h.locale}>
              <span className="font-medium">{h.ordinal}. </span>
              <Markdown>{h.body_md}</Markdown>
            </li>
          ))}
        </ol>
      )}
      {error !== null && <ErrorState message={t(errorKey(error))} />}
      {next <= data.total ? (
        <div>
          <button type="button" className="btn-secondary" disabled={pending} onClick={() => void reveal()}>
            {t("hints.reveal", { n: next, total: data.total })}
          </button>
        </div>
      ) : (
        <p className="text-sm text-slate-600">{t("hints.allShown")}</p>
      )}
    </>
  );
}

// ------------------------------------------------------------------ editorial

export function EditorialPanel({ problem }: { problem: ProblemDetail }) {
  const { t } = useI18n();
  const { state } = useSession();
  if (!problem.editorial_available) {
    return (
      <Panel id="editorial-title" title={t("editorial.title")}>
        <p className="text-slate-600">{t("editorial.none")}</p>
      </Panel>
    );
  }
  return (
    <Panel id="editorial-title" title={t("editorial.title")}>
      {state.status !== "authenticated" ? (
        <SignInPrompt text={t("errors.not_authenticated")} />
      ) : (
        <EditorialBody slug={problem.slug} unlocked={problem.editorial_unlocked} />
      )}
    </Panel>
  );
}

function EditorialBody({ slug, unlocked }: { slug: string; unlocked: boolean }) {
  const { t, locale } = useI18n();
  const [editorial, setEditorial] = useState<EditorialOut | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [confirming, setConfirming] = useState(false);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!unlocked) return;
    let cancelled = false;
    unwrap(api.GET("/api/v1/problems/{slug}/editorial", { params: { path: { slug }, query: { locale } } })).then(
      (data) => !cancelled && setEditorial(data),
      (err: unknown) => !cancelled && setError(err),
    );
    return () => {
      cancelled = true;
    };
  }, [slug, unlocked, locale]);

  async function giveUp() {
    setPending(true);
    setError(null);
    try {
      setEditorial(
        await unwrap(
          api.POST("/api/v1/problems/{slug}/editorial/unlock", {
            params: { path: { slug }, query: { locale } },
            body: { reason: "gave_up" },
          }),
        ),
      );
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
      setConfirming(false);
    }
  }

  if (error !== null) return <ErrorState message={t(errorKey(error))} />;
  if (editorial) {
    return (
      <div lang={editorial.locale} className="flex flex-col gap-2">
        {!editorial.reviewed && <p className="text-sm text-amber-900">{t("editorial.unreviewed")}</p>}
        <Markdown>{editorial.body_md}</Markdown>
      </div>
    );
  }
  if (unlocked) return <Loading />;
  return (
    <>
      <p className="text-slate-700">{t("editorial.locked")}</p>
      {confirming ? (
        <div role="group" aria-label={t("editorial.giveUp")} className="flex flex-col gap-2 rounded-md bg-amber-50 p-3">
          <p>{t("editorial.confirm")}</p>
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-primary" disabled={pending} onClick={() => void giveUp()}>
              {t("editorial.confirmYes")}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setConfirming(false)}>
              {t("editorial.cancel")}
            </button>
          </div>
        </div>
      ) : (
        <div>
          <button type="button" className="btn-secondary" onClick={() => setConfirming(true)}>
            {t("editorial.giveUp")}
          </button>
        </div>
      )}
    </>
  );
}

// ------------------------------------------------------------------ submit

function draftKey(slug: string, language: string) {
  return `ks:draft:${slug}:${language}`;
}

function readDraft(key: string): string {
  try {
    return window.localStorage.getItem(key) ?? "";
  } catch {
    return "";
  }
}

export function SubmitPanel({ problem }: { problem: ProblemDetail }) {
  const { t } = useI18n();
  const { state } = useSession();
  return (
    <Panel id="submit-title" title={t("submit.title")}>
      {state.status !== "authenticated" ? (
        <SignInPrompt text={t("submit.signIn")} />
      ) : (
        <SubmitForm problem={problem} />
      )}
    </Panel>
  );
}

function SubmitForm({ problem }: { problem: ProblemDetail }) {
  const { t, locale } = useI18n();
  const languageId = useId();
  const codeId = useId();
  const [language, setLanguage] = useState<Language>(problem.languages[0] ?? "cpp");
  const [code, setCode] = useState(() => (typeof window === "undefined" ? "" : readDraft(draftKey(problem.slug, language))));
  const [draftSaved, setDraftSaved] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<SubmissionDetail | null>(null);
  const live = useSubmissionStatus(result, locale); // polls until the checking is over

  // Drafts live only in this browser; nothing reaches the server before "Submit".
  useEffect(() => {
    const timer = setTimeout(() => {
      try {
        window.localStorage.setItem(draftKey(problem.slug, language), code);
        setDraftSaved(code.length > 0);
      } catch {
        setDraftSaved(false);
      }
    }, 500);
    return () => clearTimeout(timer);
  }, [code, language, problem.slug]);

  function switchLanguage(next: Language) {
    setLanguage(next);
    setCode(readDraft(draftKey(problem.slug, next)));
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setError(null);
    setResult(null);
    try {
      setResult(
        await unwrap(
          api.POST("/api/v1/submissions", {
            params: { query: { locale } },
            body: { problem_slug: problem.slug, language, source_code: code },
          }),
        ),
      );
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-3">
      {!problem.judge_available && <p className="text-sm text-slate-700">{t("submit.noJudge")}</p>}
      <div className="flex flex-col gap-1">
        <label htmlFor={languageId} className="font-medium">
          {t("submit.language")}
        </label>
        <select
          id={languageId}
          className="input sm:max-w-xs"
          value={language}
          onChange={(e) => switchLanguage(e.target.value as Language)}
        >
          {problem.languages.map((l) => (
            <option key={l} value={l}>
              {t(`lang.${l}` as MessageKey)}
            </option>
          ))}
        </select>
      </div>
      <div className="flex flex-col gap-1">
        <label htmlFor={codeId} className="font-medium">
          {t("submit.code")}
        </label>
        <textarea
          id={codeId}
          className="input min-h-56 font-mono text-sm"
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        {draftSaved && <p className="text-xs text-slate-600">{t("submit.draftSaved")}</p>}
      </div>
      {error !== null && (
        <ErrorState
          message={
            error instanceof ApiError && error.code === "validation_error"
              ? t("errors.source_too_large")
              : t(errorKey(error))
          }
        />
      )}
      {result && (
        <SuccessMessage>
          {result.state === "not_judged" ? t("submit.noJudge") : t("submit.saved")}{" "}
          <Link href={`/submissions/${result.id}`}>{t("submit.viewHistory")}</Link>
        </SuccessMessage>
      )}
      {live.submission && live.submission.state !== "not_judged" && (
        <div className="flex flex-col gap-2">
          {!isPending(live.submission.state) && (
            <div>
              <Outcome s={live.submission} />
            </div>
          )}
          <CheckingStatus live={live} />
        </div>
      )}
      <div>
        <button type="submit" className="btn-primary" disabled={pending || code.trim() === ""}>
          {pending ? t("submit.submitting") : t("submit.button")}
        </button>
      </div>
    </form>
  );
}

// ------------------------------------------------------------------ self-report

export function SelfReportPanel({ problem }: { problem: ProblemDetail }) {
  const { t } = useI18n();
  const { state } = useSession();
  const [done, setDone] = useState(problem.my_status === "self_reported");
  const [error, setError] = useState<unknown>(null);
  if (state.status !== "authenticated" || problem.my_status === "solved") return null;

  async function report() {
    setError(null);
    try {
      await unwrap(api.POST("/api/v1/problems/{slug}/self-report", { params: { path: { slug: problem.slug } } }));
      setDone(true);
    } catch (err) {
      setError(err);
    }
  }

  return (
    <div className="flex flex-col gap-2 text-sm">
      <p className="text-slate-600">{t("selfReport.explain")}</p>
      {error !== null && <ErrorState message={t(errorKey(error))} />}
      {done ? (
        <SuccessMessage>{t("selfReport.done")}</SuccessMessage>
      ) : (
        <div>
          <button type="button" className="btn-secondary" onClick={() => void report()}>
            {t("selfReport.button")}
          </button>
        </div>
      )}
    </div>
  );
}
