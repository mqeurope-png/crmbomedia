import { apiFetch } from "./api";

export type TaskAssignee = {
  id: string;
  full_name: string;
  email: string;
};

export type TaskContact = {
  id: string;
  first_name: string;
  last_name: string | null;
  email: string | null;
};

export type Task = {
  id: string;
  title: string;
  description: string | null;
  due_at: string | null;
  status: "pending" | "in_progress" | "done" | "cancelled";
  priority: "low" | "medium" | "high" | "urgent";
  assigned_user_id: string;
  assigned_user: TaskAssignee | null;
  contact_id: string | null;
  contact: TaskContact | null;
  company_id: string | null;
  pipeline_stage_id: string | null;
  created_by_user_id: string;
  google_event_id: string | null;
  google_calendar_id: string | null;
  reminder_minutes_before: number | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
};

export type TaskListPage = {
  items: Task[];
  total: number;
  limit: number;
  offset: number;
};

export type TaskBuckets = {
  overdue: Task[];
  today: Task[];
  tomorrow: Task[];
  later: Task[];
  no_date: Task[];
  total_open: number;
};

export type TaskCreatePayload = {
  title: string;
  description?: string | null;
  due_at?: string | null;
  status?: "pending" | "in_progress" | "done" | "cancelled";
  priority?: "low" | "medium" | "high" | "urgent";
  assigned_user_id?: string | null;
  contact_id?: string | null;
  company_id?: string | null;
  pipeline_stage_id?: string | null;
  reminder_minutes_before?: number | null;
};

export type TaskUpdatePayload = Partial<TaskCreatePayload>;

export async function listTasks(params: {
  assignedUserId?: string;
  contactId?: string;
  status?: Task["status"];
  from?: string;
  to?: string;
  skip?: number;
  limit?: number;
} = {}): Promise<TaskListPage> {
  const search = new URLSearchParams();
  if (params.assignedUserId) search.set("assigned_user_id", params.assignedUserId);
  if (params.contactId) search.set("contact_id", params.contactId);
  if (params.status) search.set("status", params.status);
  if (params.from) search.set("from", params.from);
  if (params.to) search.set("to", params.to);
  if (typeof params.skip === "number") search.set("skip", String(params.skip));
  if (typeof params.limit === "number") search.set("limit", String(params.limit));
  const qs = search.toString();
  return apiFetch<TaskListPage>(`/api/tasks${qs ? `?${qs}` : ""}`);
}

export async function getMyBuckets(): Promise<TaskBuckets> {
  return apiFetch<TaskBuckets>("/api/tasks/my-buckets");
}

export async function listContactTasks(
  contactId: string,
  options: { include_completed?: boolean } = {},
): Promise<Task[]> {
  const search = new URLSearchParams();
  if (options.include_completed) search.set("include_completed", "true");
  const qs = search.toString();
  return apiFetch<Task[]>(
    `/api/contacts/${contactId}/tasks${qs ? `?${qs}` : ""}`,
  );
}

export async function createTask(payload: TaskCreatePayload): Promise<Task> {
  return apiFetch<Task>("/api/tasks", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function updateTask(
  id: string,
  payload: TaskUpdatePayload,
): Promise<Task> {
  return apiFetch<Task>(`/api/tasks/${id}`, {
    method: "PATCH",
    body: JSON.stringify(payload),
  });
}

export async function completeTask(id: string): Promise<Task> {
  const response = await apiFetch<{ task: Task }>(
    `/api/tasks/${id}/complete`,
    { method: "POST" },
  );
  return response.task;
}

export async function deleteTask(id: string): Promise<void> {
  await apiFetch(`/api/tasks/${id}`, { method: "DELETE" });
}
