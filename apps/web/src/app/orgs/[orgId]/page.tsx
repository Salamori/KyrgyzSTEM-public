import { OrgView } from "@/components/orgs/OrgView";

export default async function OrgPage({ params }: { params: Promise<{ orgId: string }> }) {
  const { orgId } = await params;
  return <OrgView orgId={orgId} />;
}
