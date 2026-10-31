import { GroupView } from "@/components/orgs/GroupView";

export default async function GroupPage({ params }: { params: Promise<{ orgId: string; groupId: string }> }) {
  const { orgId, groupId } = await params;
  return <GroupView orgId={orgId} groupId={groupId} />;
}
