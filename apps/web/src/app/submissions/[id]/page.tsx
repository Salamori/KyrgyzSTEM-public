import { SubmissionView } from "@/components/olympiad/Submissions";

export default async function SubmissionPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <SubmissionView id={id} />;
}
