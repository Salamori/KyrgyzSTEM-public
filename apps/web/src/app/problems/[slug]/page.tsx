import { ProblemView } from "@/components/olympiad/ProblemView";

export default async function ProblemPage({ params }: { params: Promise<{ slug: string }> }) {
  const { slug } = await params;
  return <ProblemView slug={slug} />;
}
