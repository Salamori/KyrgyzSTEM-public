"use client";

import { useId, useState, type FormEvent } from "react";

import { api, unwrap, type Band, type ProblemDetail, type Schemas } from "@/lib/api/client";
import { LOCALE_NAMES, formatDate, isLocale, type MessageKey } from "@/lib/i18n";
import { useI18n } from "@/lib/i18n/context";
import { useLoad } from "@/lib/useLoad";

import { errorKey } from "../errorMessage";
import { Markdown } from "../Markdown";
import { ErrorState, Loading, SuccessMessage } from "../states";

const BANDS: Band[] = ["intro", "easy", "medium", "hard", "challenge"];

type AdminEditorial = Schemas["AdminEditorialOut"];
type AdminHint = Schemas["AdminHintOut"];
type PublishBlocker = Schemas["PublishBlocker"];
type Locale = Schemas["Locale"];

/** Admin-only. Every action here is enforced and audited by the API. */
export function ReviewPanel({
  problem,
  onChange,
}: {
  problem: ProblemDetail;
  onChange: (p: ProblemDetail) => void;
}) {
  const { t } = useI18n();
  const bandId = useId();
  const rationaleId = useId();
  const [error, setError] = useState<unknown>(null);
  const [saved, setSaved] = useState(false);
  const [pending, setPending] = useState(false);
  const review = problem.review;
  if (!review) return null;

  const name = (loc: string) => (isLocale(loc) ? LOCALE_NAMES[loc] : loc);

  async function run(action: () => Promise<ProblemDetail>) {
    setPending(true);
    setError(null);
    setSaved(false);
    try {
      onChange(await action());
      setSaved(true);
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  }

  const path = { params: { path: { slug: problem.slug } } };

  function onDifficulty(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    void run(() =>
      unwrap(
        api.PATCH("/api/v1/admin/problems/{slug}/difficulty", {
          ...path,
          body: { band: String(form.get("band")) as Band, rationale: String(form.get("rationale")) },
        }),
      ),
    );
  }

  return (
    <section aria-labelledby="review-title" className="card flex flex-col gap-3 border-slate-400">
      <h2 id="review-title" className="text-lg font-semibold">
        {t("review.title")}
      </h2>
      <Readiness problem={problem} />
      <p className="text-sm">
        {t("review.reviewed", {
          languages: review.reviewed_locales.map(name).join(", ") || t("review.none"),
        })}
      </p>
      <div className="flex flex-wrap gap-2">
        {problem.available_locales
          .filter((loc) => !review.reviewed_locales.includes(loc))
          .map((loc) => (
            <button
              key={loc}
              type="button"
              className="btn-secondary"
              disabled={pending}
              onClick={() =>
                void run(() =>
                  unwrap(
                    api.POST("/api/v1/admin/problems/{slug}/translations/{locale}/review", {
                      params: { path: { slug: problem.slug, locale: loc } },
                    }),
                  ),
                )
              }
            >
              {t("review.reviewTranslation", { language: name(loc) })}
            </button>
          ))}
      </div>

      <RightsCheck
        problem={problem}
        pending={pending}
        onConfirm={(attest) =>
          void run(() =>
            unwrap(
              api.POST("/api/v1/admin/problems/{slug}/rights/verify", { ...path, body: { attest_not_author: attest } }),
            ),
          )
        }
      />

      <EditorialReview
        problem={problem}
        pending={pending}
        onReview={(locale, attest) =>
          void run(() =>
            unwrap(
              api.POST("/api/v1/admin/problems/{slug}/editorials/{locale}/review", {
                params: { path: { slug: problem.slug, locale } },
                body: { attest_not_author: attest },
              }),
            ),
          )
        }
      />

      <HintReview
        problem={problem}
        pending={pending}
        onReviewOne={(locale, ordinal, attest) =>
          run(() =>
            unwrap(
              api.POST("/api/v1/admin/problems/{slug}/hints/{locale}/{ordinal}/review", {
                params: { path: { slug: problem.slug, locale, ordinal } },
                body: { attest_not_author: attest },
              }),
            ),
          )
        }
        onReviewAll={(locale, attest) =>
          run(() =>
            unwrap(
              api.POST("/api/v1/admin/problems/{slug}/hints/{locale}/review", {
                params: { path: { slug: problem.slug, locale } },
                body: { attest_not_author: attest },
              }),
            ),
          )
        }
      />

      <form onSubmit={onDifficulty} className="flex flex-col gap-2">
        <p className="text-sm text-slate-600">{t("review.difficultyCovers")}</p>
        <label htmlFor={bandId} className="font-medium">
          {t("problems.band")}
        </label>
        <select id={bandId} name="band" defaultValue={problem.difficulty_band} className="input sm:max-w-xs">
          {BANDS.map((b) => (
            <option key={b} value={b}>
              {t(`band.${b}` as MessageKey)}
            </option>
          ))}
        </select>
        <label htmlFor={rationaleId} className="font-medium">
          {t("review.difficultyRationale")}
        </label>
        <textarea
          id={rationaleId}
          name="rationale"
          className="input"
          minLength={10}
          maxLength={500}
          required
          defaultValue={problem.difficulty_rationale}
        />
        <div>
          <button type="submit" className="btn-secondary" disabled={pending}>
            {t("review.saveDifficulty")}
          </button>
        </div>
      </form>

      {error !== null && <ErrorState message={t(errorKey(error))} />}
      {saved && <SuccessMessage>{t("profile.saved")}</SuccessMessage>}

      <div>
        {problem.status === "published" ? (
          <button
            type="button"
            className="btn-secondary"
            disabled={pending}
            onClick={() => void run(() => unwrap(api.POST("/api/v1/admin/problems/{slug}/unpublish", path)))}
          >
            {t("review.unpublish")}
          </button>
        ) : (
          <button
            type="button"
            className="btn-primary"
            disabled={pending || review.publish_blockers.length > 0}
            onClick={() => void run(() => unwrap(api.POST("/api/v1/admin/problems/{slug}/publish", path)))}
          >
            {t("review.publish")}
          </button>
        )}
      </div>
    </section>
  );
}

/** A human confirms the right to publish under the declared license; nothing is assumed. */
function RightsCheck({
  problem,
  pending,
  onConfirm,
}: {
  problem: ProblemDetail;
  pending: boolean;
  onConfirm: (attestNotAuthor: boolean) => void;
}) {
  const { t, locale } = useI18n();
  const checkId = useId();
  const attestId = useId();
  const [checked, setChecked] = useState(false);
  const [attested, setAttested] = useState(false);
  const verifiedAt = problem.review?.rights_verified_at;
  // Imported problems usually have no author account: the server cannot check identity,
  // so the reviewer states it (recorded in the audit log as attested, not verified).
  const unlinked = problem.review?.author_linked === false;
  return (
    <div className="flex flex-col gap-2 rounded-md border border-slate-200 p-3 text-sm">
      <p className="font-medium">{t("review.rightsTitle")}</p>
      <p>
        {t("review.rightsOrigin", { origin: t(`origin.${problem.origin}` as MessageKey), license: problem.license })}
        {problem.generated_with_ai && ` ${t("review.rightsAi")}`}
      </p>
      {problem.source && (
        <p>
          {t("problem.source", {
            title: problem.source.title,
            author: problem.source.author,
            license: problem.source.license,
          })}{" "}
          <span className="break-all">{problem.source.url}</span>
        </p>
      )}
      {verifiedAt ? (
        <p>{t("review.rightsVerified", { date: formatDate(locale, verifiedAt) })}</p>
      ) : (
        <>
          <div className="flex items-start gap-2">
            <input id={checkId} type="checkbox" checked={checked} onChange={(e) => setChecked(e.target.checked)} />
            <label htmlFor={checkId}>{t("review.rightsConfirmLabel", { license: problem.license })}</label>
          </div>
          {unlinked && (
            <>
              <p className="text-amber-900">{t("review.authorUnlinked")}</p>
              <div className="flex items-start gap-2">
                <input id={attestId} type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
                <label htmlFor={attestId}>{t("review.attestNotAuthor")}</label>
              </div>
            </>
          )}
          <div>
            <button
              type="button"
              className="btn-secondary"
              disabled={pending || !checked || (unlinked && !attested)}
              onClick={() => onConfirm(unlinked && attested)}
            >
              {t("review.rightsConfirm")}
            </button>
          </div>
        </>
      )}
    </div>
  );
}

/**
 * Editorial review. The text comes from the admin endpoint, never the student unlock flow,
 * so reading or reviewing it records no unlock or progress for the reviewer.
 */
function EditorialReview({
  problem,
  pending,
  onReview,
}: {
  problem: ProblemDetail;
  pending: boolean;
  onReview: (locale: Locale, attestNotAuthor: boolean) => void;
}) {
  const { t } = useI18n();
  const editorials = useLoad<AdminEditorial[]>(
    () =>
      unwrap(
        api.GET("/api/v1/admin/problems/{slug}/editorials", { params: { path: { slug: problem.slug } } }),
      ),
    [problem.slug],
  );
  const reviewed = problem.review?.reviewed_editorial_locales ?? [];
  const unlinked = problem.review?.author_linked === false;
  return (
    <div className="flex flex-col gap-2 rounded-md border border-slate-200 p-3 text-sm">
      <p className="font-medium">{t("review.editorialsTitle")}</p>
      <p className="text-slate-600">{t("review.editorialsNote")}</p>
      {editorials.state.status === "loading" && <Loading />}
      {editorials.state.status === "error" && (
        <ErrorState message={t(errorKey(editorials.state.error))} onRetry={() => void editorials.reload()} />
      )}
      {editorials.state.status === "ready" && editorials.state.data.length === 0 && (
        <p>{t("review.editorialsNone")}</p>
      )}
      {editorials.state.status === "ready" &&
        editorials.state.data.map((e) => (
          <EditorialItem
            key={e.locale}
            editorial={e}
            reviewed={reviewed.includes(e.locale)}
            unlinked={unlinked}
            pending={pending}
            onReview={onReview}
          />
        ))}
    </div>
  );
}

function EditorialItem({
  editorial,
  reviewed,
  unlinked,
  pending,
  onReview,
}: {
  editorial: AdminEditorial;
  reviewed: boolean;
  unlinked: boolean;
  pending: boolean;
  onReview: (locale: Locale, attestNotAuthor: boolean) => void;
}) {
  const { t } = useI18n();
  const readId = useId();
  const attestId = useId();
  const [read, setRead] = useState(false);
  const [attested, setAttested] = useState(false);
  const language = isLocale(editorial.locale) ? LOCALE_NAMES[editorial.locale] : editorial.locale;
  return (
    <section aria-label={t("review.editorialLabel", { language })} className="flex flex-col gap-2 border-t border-slate-200 pt-2">
      <p className="font-medium">
        {language}: {reviewed ? t("review.editorialReviewed") : t("review.editorialNotReviewed")}
      </p>
      <div className="max-h-64 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-2">
        <Markdown links={false}>{editorial.body_md}</Markdown>
      </div>
      <div className="flex items-start gap-2">
        <input id={readId} type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} />
        <label htmlFor={readId}>{t("review.editorialRead", { language })}</label>
      </div>
      {unlinked && (
        <div className="flex items-start gap-2">
          <input id={attestId} type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
          <label htmlFor={attestId}>{t("review.attestNotAuthor")}</label>
        </div>
      )}
      <div>
        <button
          type="button"
          className="btn-secondary"
          disabled={pending || !read || (unlinked && !attested)}
          onClick={() => onReview(editorial.locale, unlinked && attested)}
        >
          {t("review.reviewEditorial", { language })}
        </button>
      </div>
    </section>
  );
}

/**
 * Hint review, one collapsible section per language. The text comes from the admin endpoint,
 * never the student reveal flow, so reading or reviewing records no reveal or progress.
 */
function HintReview({
  problem,
  pending,
  onReviewOne,
  onReviewAll,
}: {
  problem: ProblemDetail;
  pending: boolean;
  onReviewOne: (locale: Locale, ordinal: number, attestNotAuthor: boolean) => Promise<void>;
  onReviewAll: (locale: Locale, attestNotAuthor: boolean) => Promise<void>;
}) {
  const { t } = useI18n();
  const hints = useLoad<AdminHint[]>(
    () => unwrap(api.GET("/api/v1/admin/problems/{slug}/hints", { params: { path: { slug: problem.slug } } })),
    [problem.slug],
  );
  const unlinked = problem.review?.author_linked === false;
  const byLocale = new Map<Locale, AdminHint[]>();
  if (hints.state.status === "ready") {
    for (const h of hints.state.data) byLocale.set(h.locale, [...(byLocale.get(h.locale) ?? []), h]);
  }
  return (
    <div className="flex flex-col gap-2 rounded-md border border-slate-200 p-3 text-sm">
      <p className="font-medium">{t("review.hintsTitle")}</p>
      <p className="text-slate-600">{t("review.hintsNote")}</p>
      {hints.state.status === "loading" && <Loading />}
      {hints.state.status === "error" && (
        <ErrorState message={t(errorKey(hints.state.error))} onRetry={() => void hints.reload()} />
      )}
      {hints.state.status === "ready" && byLocale.size === 0 && <p>{t("review.hintsNone")}</p>}
      {[...byLocale].map(([locale, items]) => (
        <HintLocale
          key={locale}
          locale={locale}
          items={items}
          unlinked={unlinked}
          pending={pending}
          onReviewOne={(ordinal, attest) => onReviewOne(locale, ordinal, attest).then(() => hints.reload())}
          onReviewAll={(attest) => onReviewAll(locale, attest).then(() => hints.reload())}
        />
      ))}
    </div>
  );
}

function HintLocale({
  locale,
  items,
  unlinked,
  pending,
  onReviewOne,
  onReviewAll,
}: {
  locale: Locale;
  items: AdminHint[];
  unlinked: boolean;
  pending: boolean;
  onReviewOne: (ordinal: number, attestNotAuthor: boolean) => Promise<void>;
  onReviewAll: (attestNotAuthor: boolean) => Promise<void>;
}) {
  const { t } = useI18n();
  const readId = useId();
  const attestId = useId();
  const [read, setRead] = useState(false);
  const [attested, setAttested] = useState(false);
  const language = isLocale(locale) ? LOCALE_NAMES[locale] : locale;
  const ready = read && (!unlinked || attested);
  const reviewed = items.filter((h) => h.reviewed).length;
  return (
    <details className="border-t border-slate-200 pt-2">
      <summary className="cursor-pointer font-medium">
        {t("review.hintsSummary", { language, reviewed, total: items.length })}
      </summary>
      <section aria-label={t("review.hintsLabel", { language })} className="mt-2 flex flex-col gap-2">
        <ol className="flex flex-col gap-2">
          {items.map((h) => (
            <li key={h.ordinal} className="flex flex-col gap-1">
              <p className="text-xs text-slate-600">
                {t("review.hintNumber", { n: h.ordinal })} ·{" "}
                {h.reviewed ? t("review.hintReviewed") : t("review.hintNotReviewed")}
              </p>
              <div className="rounded-md border border-slate-200 bg-slate-50 p-2">
                <Markdown links={false}>{h.body_md}</Markdown>
              </div>
              <div>
                <button
                  type="button"
                  className="btn-secondary"
                  disabled={pending || !ready}
                  onClick={() => void onReviewOne(h.ordinal, unlinked && attested)}
                >
                  {t("review.reviewHint", { n: h.ordinal, language })}
                </button>
              </div>
            </li>
          ))}
        </ol>
        <div className="flex items-start gap-2">
          <input id={readId} type="checkbox" checked={read} onChange={(e) => setRead(e.target.checked)} />
          <label htmlFor={readId}>{t("review.hintsRead", { language })}</label>
        </div>
        {unlinked && (
          <div className="flex items-start gap-2">
            <input id={attestId} type="checkbox" checked={attested} onChange={(e) => setAttested(e.target.checked)} />
            <label htmlFor={attestId}>{t("review.attestNotAuthor")}</label>
          </div>
        )}
        <div>
          <button
            type="button"
            className="btn-secondary"
            disabled={pending || !ready}
            onClick={() => void onReviewAll(unlinked && attested)}
          >
            {t("review.reviewAllHints", { language })}
          </button>
        </div>
      </section>
    </details>
  );
}

/**
 * Publication readiness, computed by the API's publication gate (the same rule that refuses
 * a publish): how much of each part is reviewed, then every remaining blocker with the
 * language or hint it applies to.
 */
function Readiness({ problem }: { problem: ProblemDetail }) {
  const { t } = useI18n();
  const review = problem.review;
  if (!review) return null;
  const name = (loc: string) => (isLocale(loc) ? LOCALE_NAMES[loc] : loc);
  const hintTotal = review.hint_review.reduce((sum, h) => sum + h.total, 0);
  const hintReviewed = review.hint_review.reduce((sum, h) => sum + h.reviewed, 0);
  const fraction = (reviewed: number, total: number) => t("review.fraction", { reviewed, total });
  const where = (b: PublishBlocker) => {
    if (b.locale && b.ordinal) return `${t("review.hintNumber", { n: b.ordinal })}, ${name(b.locale)}`;
    return b.locale ? name(b.locale) : null;
  };
  return (
    <div className="flex flex-col gap-1">
      <p className="text-sm">
        {t("review.progress", {
          statements: fraction(review.reviewed_locales.length, problem.available_locales.length),
          editorials: fraction(review.reviewed_editorial_locales.length, review.editorial_locales.length),
          hints: fraction(hintReviewed, hintTotal),
        })}
      </p>
      {review.blocking_items.length > 0 ? (
        <div>
          <p className="font-medium">{t("review.blockers")}</p>
          <ul className="list-disc pl-6 text-sm">
            {review.blocking_items.map((b) => {
              const what = t(`review.blocker.${b.code}` as MessageKey);
              const at = where(b);
              return (
                <li key={`${b.code}-${b.item}-${b.locale ?? ""}-${b.ordinal ?? ""}`}>
                  {at ? t("review.blockerAt", { what, where: at }) : what}
                </li>
              );
            })}
          </ul>
        </div>
      ) : (
        <p>{t("review.ready")}</p>
      )}
    </div>
  );
}
