"use client";

import Link from "next/link";
import { useId, useState, type FormEvent } from "react";

import { api, unwrap, type AssistantAnswer, type AssistantStatus, type ProblemDetail } from "@/lib/api/client";
import type { MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useSession } from "@/lib/session";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { Markdown } from "../Markdown";
import { ErrorState, Loading } from "../states";

const LEVELS = [1, 2, 3, 4] as const;
const LIMIT_OUTCOMES = new Set(["quota_exceeded", "budget_exhausted"]);
const WITHHELD_OUTCOMES = new Set(["code_withheld", "editorial_overlap"]);

/** Optional AI help. Renders nothing for visitors; the platform works fully without it. */
export function AssistantPanel({ problem }: { problem: ProblemDetail }) {
  const { state } = useSession();
  if (state.status !== "authenticated" || problem.status !== "published") return null;
  return <AssistantBody problem={problem} />;
}

function AssistantBody({ problem }: { problem: ProblemDetail }) {
  const { t } = useI18n();
  const status = useLoad<AssistantStatus>(() => unwrap(api.GET("/api/v1/assistant/status")), []);

  return (
    <section aria-labelledby="assistant-title" className="card flex flex-col gap-3">
      <h2 id="assistant-title" className="text-lg font-semibold">
        {t("assistant.title")}
      </h2>
      {status.state.status === "loading" && <Loading />}
      {status.state.status === "error" && (
        <ErrorState message={t(errorKey(status.state.error))} onRetry={() => void status.reload()} />
      )}
      {status.state.status === "ready" &&
        (status.state.data.enabled ? (
          <AssistantForm problem={problem} status={status.state.data} />
        ) : (
          <p className="text-slate-600">{t("assistant.off")}</p>
        ))}
    </section>
  );
}

function AssistantForm({ problem, status }: { problem: ProblemDetail; status: AssistantStatus }) {
  const { t, locale } = useI18n();
  const questionId = useId();
  const levelName = useId();
  const [level, setLevel] = useState<(typeof LEVELS)[number]>(1);
  const [question, setQuestion] = useState("");
  const [attach, setAttach] = useState(false);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [answer, setAnswer] = useState<AssistantAnswer | null>(null);
  const [usage, setUsage] = useState({ used: status.used_today, limit: status.daily_limit });

  async function latestSubmissionId(): Promise<string | undefined> {
    const page = await unwrap(
      api.GET("/api/v1/submissions", { params: { query: { problem: problem.slug, limit: 1, offset: 0 } } }),
    );
    return page.items[0]?.id;
  }

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    setPending(true);
    setError(null);
    try {
      const submission_id = attach ? await latestSubmissionId() : undefined;
      const result = await unwrap(
        api.POST("/api/v1/problems/{slug}/assistant", {
          params: { path: { slug: problem.slug }, query: { locale } },
          body: { level, question: question.trim() || undefined, submission_id },
        }),
      );
      setAnswer(result);
      setUsage({ used: result.used_today, limit: result.daily_limit });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <p className="text-sm text-slate-700">{t("assistant.intro")}</p>
      <p className="text-sm text-slate-700">{t("assistant.noCodeCheck")}</p>
      <form onSubmit={(e) => void onSubmit(e)} className="flex flex-col gap-3">
        <fieldset className="flex flex-col gap-2">
          <legend className="font-medium">{t("assistant.levelLabel")}</legend>
          <div className="flex flex-wrap gap-x-4 gap-y-2">
            {LEVELS.map((l) => {
              const locked = l === 4 && !problem.editorial_unlocked;
              return (
                <label key={l} className={`flex items-center gap-2 ${locked ? "text-slate-400" : ""}`}>
                  <input
                    type="radio"
                    name={levelName}
                    value={l}
                    checked={level === l}
                    disabled={locked}
                    onChange={() => setLevel(l)}
                  />
                  {t(`assistant.level${l}` as MessageKey)}
                </label>
              );
            })}
          </div>
          {!problem.editorial_unlocked && <p className="text-xs text-slate-600">{t("assistant.level4Locked")}</p>}
        </fieldset>
        <div className="flex flex-col gap-1">
          <label htmlFor={questionId} className="font-medium">
            {t("assistant.question")}
          </label>
          <textarea
            id={questionId}
            className="input min-h-20"
            maxLength={500}
            value={question}
            aria-describedby={`${questionId}-hint`}
            onChange={(e) => setQuestion(e.target.value)}
          />
          <p id={`${questionId}-hint`} className="text-xs text-slate-600">
            {t("assistant.questionHint")}
          </p>
        </div>
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" checked={attach} onChange={(e) => setAttach(e.target.checked)} />
          {t("assistant.attach")}
        </label>
        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" className="btn-primary" disabled={pending}>
            {pending ? t("assistant.asking") : t("assistant.ask")}
          </button>
          <span className="text-xs text-slate-600">{t("assistant.usage", usage)}</span>
        </div>
      </form>
      {error !== null && <ErrorState message={t(errorKey(error))} />}
      {answer && <AnswerView answer={answer} />}
    </>
  );
}

function AnswerView({ answer }: { answer: AssistantAnswer }) {
  const { t } = useI18n();
  if (answer.mode === "fallback") {
    const message = LIMIT_OUTCOMES.has(answer.outcome)
      ? t("assistant.fallbackLimit")
      : WITHHELD_OUTCOMES.has(answer.outcome)
        ? t("assistant.withheld")
        : t("assistant.fallback");
    return (
      <div role="status" aria-live="polite" className="flex flex-col gap-2 rounded-md bg-slate-50 p-3">
        <p>{message}</p>
        {answer.fallback_hints.length === 0 ? (
          <p className="text-slate-600">{t("assistant.noHints")}</p>
        ) : (
          <ol className="flex flex-col gap-2">
            {answer.fallback_hints.map((h) => (
              <li key={h.ordinal} lang={h.locale} className="rounded-md bg-sky-50 p-3">
                <span className="font-medium">{h.ordinal}. </span>
                <Markdown>{h.body_md}</Markdown>
              </li>
            ))}
          </ol>
        )}
      </div>
    );
  }
  return (
    <div role="status" aria-live="polite" lang={answer.language} className="flex flex-col gap-3">
      {answer.language_note && <p className="text-sm text-amber-900">{t("assistant.languageNote")}</p>}
      {answer.grounded.length > 0 && (
        <div>
          <h3 className="font-medium">{t("assistant.grounded")}</h3>
          <ul className="mt-1 flex flex-col gap-2">
            {answer.grounded.map((g, i) => (
              <li key={i} className="rounded-md border border-slate-200 p-2">
                <p>{g.text}</p>
                <p className="text-xs text-slate-600">
                  {g.sources.map((s) => t("assistant.source", { label: s.label })).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
      {answer.suggestion && (
        <div className="rounded-md border border-amber-200 bg-amber-50 p-3">
          <p className="mb-1 text-xs font-medium text-amber-900">{t("assistant.suggestion")}</p>
          <Markdown links={false}>{answer.suggestion}</Markdown>
        </div>
      )}
      {answer.insufficient_sources && <p className="text-sm text-slate-700">{t("assistant.insufficient")}</p>}
      {answer.model_id && (
        <p className="text-xs text-slate-500">
          {t("assistant.model", { model: answer.model_id, version: answer.prompt_version })}
        </p>
      )}
      <p className="text-xs">
        <Link href="#hints-title">{t("hints.title")}</Link>
      </p>
    </div>
  );
}
