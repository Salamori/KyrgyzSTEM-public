import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { OrgGroupOut, SessionOut } from "@/lib/api/client";
import { anonymous, makeUser, mockApi, renderWithProviders, sessionFor } from "@/test/utils";

import { AdminOrgs } from "../admin/AdminOrgs";
import { GroupView } from "./GroupView";
import { OrgsHome } from "./OrgsHome";

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

const ORG = "00000000-0000-4000-8000-0000000000o1";
const GROUP = "00000000-0000-4000-8000-0000000000g1";
const SESSION = "00000000-0000-4000-8000-0000000000s1";
const KID = "00000000-0000-4000-8000-0000000000k1";
const base = `/api/v1/orgs/${ORG}/groups/${GROUP}`;

function makeGroup(overrides: Partial<OrgGroupOut> = {}): OrgGroupOut {
  return {
    id: GROUP,
    org_id: ORG,
    name: "[Synthetic] Club",
    kind: "club",
    lead_name: "[Synthetic] Teacher",
    member_count: 1,
    can_manage: false,
    join_code_expires_at: null,
    ...overrides,
  };
}

function makeSession(overrides: Partial<SessionOut> = {}): SessionOut {
  return {
    id: SESSION,
    starts_at: "2026-10-20T10:00:00Z",
    title: "Graphs",
    present: null,
    absent: null,
    excused: null,
    unmarked: null,
    my_status: "present",
    ...overrides,
  };
}

describe("OrgsHome", () => {
  it("asks anonymous visitors to sign in", async () => {
    mockApi({ "GET /api/v1/auth/session": anonymous });
    renderWithProviders(<OrgsHome />);
    expect(await screen.findByRole("link", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows empty states, then joins with a code", async () => {
    let joined = false;
    const fetch = mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      "GET /api/v1/orgs": () => ({
        status: 200,
        body: joined ? [{ id: ORG, name: "[Synthetic] School", kind: "school", is_synthetic: true, my_role: "student" }] : [],
      }),
      "GET /api/v1/me/attendance": { status: 200, body: [] },
      "POST /api/v1/groups/join": () => {
        joined = true;
        return { status: 200, body: { org_id: ORG, group_id: GROUP, group_name: "[Synthetic] Club" } };
      },
    });
    renderWithProviders(<OrgsHome />);
    expect(await screen.findByText(/not in any organization/)).toBeInTheDocument();
    expect(screen.getByText("No attendance recorded yet.")).toBeInTheDocument();
    await userEvent.type(screen.getByLabelText("Join code from your teacher"), "abcd2345");
    await userEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(await screen.findByText("You joined [Synthetic] Club.")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "[Synthetic] School" })).toBeInTheDocument();
    const post = fetch.mock.calls.map(([r]) => r as Request).find((r) => r.method === "POST");
    expect(await post?.json()).toEqual({ code: "ABCD2345" });
  });

  it("shows a localized error for a wrong code", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      "GET /api/v1/orgs": { status: 200, body: [] },
      "GET /api/v1/me/attendance": { status: 200, body: [] },
      "POST /api/v1/groups/join": {
        status: 404,
        body: { error: { code: "invalid_join_code", message: "server text", details: null } },
      },
    });
    renderWithProviders(<OrgsHome />);
    await userEvent.type(await screen.findByLabelText("Join code from your teacher"), "WRONG234");
    await userEvent.click(screen.getByRole("button", { name: "Join" }));
    expect(await screen.findByText("This code is wrong or has expired.")).toBeInTheDocument();
    expect(screen.queryByText("server text")).not.toBeInTheDocument();
  });
});

describe("GroupView", () => {
  it("shows a student only their own status, with no teacher tools", async () => {
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      [`GET ${base}`]: { status: 200, body: makeGroup() },
      [`GET ${base}/sessions`]: { status: 200, body: [makeSession()] },
    });
    renderWithProviders(<GroupView orgId={ORG} groupId={GROUP} />);
    expect(await screen.findByText("Present")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Take attendance" })).not.toBeInTheDocument();
    expect(screen.queryByText("Download CSV")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Leave this group" })).toBeInTheDocument();
  });

  it("asks for confirmation before a student leaves", async () => {
    const fetch = mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser()),
      [`GET ${base}`]: { status: 200, body: makeGroup() },
      [`GET ${base}/sessions`]: { status: 200, body: [] },
    });
    renderWithProviders(<GroupView orgId={ORG} groupId={GROUP} />);
    await userEvent.click(await screen.findByRole("button", { name: "Leave this group" }));
    expect(screen.getByText(/deletes your attendance records/)).toBeInTheDocument();
    expect(fetch.mock.calls.some(([r]) => (r as Request).method === "DELETE")).toBe(false);
  });

  it("shows not found for a group in another organization", async () => {
    mockApi({ "GET /api/v1/auth/session": sessionFor(makeUser()) });
    renderWithProviders(<GroupView orgId={ORG} groupId={GROUP} />);
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("lets the teacher mark attendance", async () => {
    let body: unknown = null;
    mockApi({
      "GET /api/v1/auth/session": sessionFor(makeUser({ role: "teacher" })),
      [`GET ${base}`]: { status: 200, body: makeGroup({ can_manage: true }) },
      [`GET ${base}/sessions`]: {
        status: 200,
        body: [makeSession({ present: 0, absent: 0, excused: 0, unmarked: 1, my_status: null })],
      },
      [`GET ${base}/summary`]: {
        status: 200,
        body: { sessions: 1, members: 1, marked: 0, present: 0, attendance_rate: null },
      },
      [`GET ${base}/members`]: {
        status: 200,
        body: [{ user_id: KID, display_name: "[Synthetic] Kid", joined_at: "2026-10-01T00:00:00Z" }],
      },
      [`GET ${base}/sessions/${SESSION}/attendance`]: {
        status: 200,
        body: [{ user_id: KID, display_name: "[Synthetic] Kid", status: null }],
      },
      [`PUT ${base}/sessions/${SESSION}/attendance`]: (req) => {
        void req.json().then((b) => (body = b));
        return { status: 200, body: [{ user_id: KID, display_name: "[Synthetic] Kid", status: "absent" }] };
      },
    });
    renderWithProviders(<GroupView orgId={ORG} groupId={GROUP} />);
    expect(await screen.findByText(/Present: no data yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Download CSV" })).toHaveAttribute("href", `${base}/attendance.csv`);
    await userEvent.click(screen.getByRole("button", { name: "Take attendance" }));
    const group = await screen.findByRole("group", { name: "[Synthetic] Kid" });
    await userEvent.click(within(group).getByLabelText("Absent"));
    await userEvent.click(screen.getByRole("button", { name: "Save attendance" }));
    await waitFor(() => expect(body).toEqual({ records: [{ user_id: KID, status: "absent" }] }));
  });
});

describe("AdminOrgs", () => {
  it("lists organizations with counts only", async () => {
    mockApi({
      "GET /api/v1/admin/orgs": {
        status: 200,
        body: [
          { id: ORG, name: "[Synthetic] School", kind: "school", is_synthetic: true, admins: 1, teachers: 2, students: 30, groups: 3 },
        ],
      },
    });
    renderWithProviders(<AdminOrgs />);
    expect(await screen.findByText("[Synthetic] School")).toBeInTheDocument();
    expect(screen.getByText(/Students 30/)).toBeInTheDocument();
  });
});
