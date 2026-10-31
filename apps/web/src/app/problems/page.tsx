import { ProblemBank } from "@/components/olympiad/ProblemBank";

type Search = Promise<Record<string, string | string[] | undefined>>;

const first = (v: string | string[] | undefined) => (Array.isArray(v) ? v[0] : v);

export default async function ProblemsPage({ searchParams }: { searchParams: Search }) {
  const params = await searchParams;
  return (
    <ProblemBank
      initial={{ q: first(params.q), topic: first(params.topic), band: first(params.band) }}
    />
  );
}
