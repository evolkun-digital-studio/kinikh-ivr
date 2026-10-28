// Types mirror the Pydantic response models in app/api/v1/*.py exactly.
// Keep in sync with the backend — these are not independently validated.

export interface Paginated<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface DepartmentBrief {
  id: string;
  name: string;
  display_name: string;
}

export interface CallBrief {
  id: string;
  call_sid: string;
  status: string;
}

export interface LeadBrief {
  id: string;
  name: string | null;
  phone: string;
  department_id: string | null;
  department_name: string | null;
  language: string | null;
}

export interface Department {
  id: string;
  name: string;
  display_name: string;
  email: string;
  greeting: string | null;
  is_active: boolean;
  call_count: number;
  lead_count: number;
}

export type CallStatus = "initiated" | "ringing" | "in_progress" | "completed" | "failed";

export interface Call {
  id: string;
  call_sid: string;
  caller_number: string;
  callee_number: string | null;
  status: string;
  direction: string;
  duration_seconds: number | null;
  provider: string;
  started_at: string | null;
  ended_at: string | null;
  created_at: string;
  lead: LeadBrief | null;
}

export interface CostBreakdown {
  telephony_cost_usd: number;
  openai_cost_usd: number;
  voice_cost_usd: number;
  email_cost_usd: number;
  total_cost_usd: number;
  duration_seconds: number | null;
}

export interface RecordingInfo {
  id: string;
  has_audio: boolean;
  status: string;
  duration: number | null;
}

export interface CallDetail extends Call {
  transcript: string | null;
  error_message: string | null;
  cost: CostBreakdown | null;
  recording: RecordingInfo | null;
}

export interface EmailLogBrief {
  id: string;
  recipient_email: string;
  status: string;
  sent_at: string | null;
  retry_count: number;
  error_message: string | null;
}

export interface Lead {
  id: string;
  name: string | null;
  phone: string;
  email: string | null;
  requirement: string | null;
  summary: string | null;
  language: string | null;
  department_id: string | null;
  department: DepartmentBrief | null;
  call: CallBrief | null;
  created_at: string;
}

export interface LeadDetail extends Lead {
  additional_notes: string | null;
  raw_transcript: string | null;
  email_logs: EmailLogBrief[];
}

export interface Recording {
  id: string;
  call_sid: string;
  caller_number: string;
  department: string | null;
  language: string;
  start_time: string;
  end_time: string | null;
  duration: number | null;
  status: string;
  has_audio: boolean;
}

export interface RecordingDetail extends Recording {
  transcript: string | null;
  summary: string | null;
  errors: string | null;
}

export interface EmailLog {
  id: string;
  recipient_email: string;
  subject: string;
  status: string;
  sent_at: string | null;
  retry_count: number;
  error_message: string | null;
  created_at: string;
  lead: {
    id: string;
    name: string | null;
    phone: string;
    department_name: string | null;
  } | null;
}

export interface CostTotals {
  telephony_usd: number;
  openai_usd: number;
  voice_usd: number;
  email_usd: number;
  total_usd: number;
  call_count: number;
  cost_per_call_usd: number;
}

export interface ProviderUsage {
  provider: string;
  requests: number;
  total_tokens: number;
  total_characters: number;
  estimated_cost_usd: number;
}

export interface UsageSummary {
  totals: CostTotals;
  by_provider: ProviderUsage[];
}

export interface CostPoint {
  date: string;
  cost_usd: number;
}

export interface TimeseriesPoint {
  date: string;
  count: number;
}

export interface DepartmentCount {
  department_id: string | null;
  department_name: string | null;
  count: number;
}

export interface AnalyticsOverview {
  calls_over_time: TimeseriesPoint[];
  leads_over_time: TimeseriesPoint[];
  calls_by_department: DepartmentCount[];
  calls_by_language: Record<string, number>;
  completed_vs_failed: Record<string, number>;
  email_status_breakdown: Record<string, number>;
  cost_over_time: CostPoint[];
}

export interface DashboardKPIs {
  total_calls: number;
  completed_calls: number;
  failed_calls: number;
  total_leads: number;
  today_calls: number;
  today_leads: number;
  total_cost_usd: number;
  average_call_duration_seconds: number | null;
}

export interface DashboardSummary {
  kpis: DashboardKPIs;
  recent_calls: Call[];
  recent_leads: Lead[];
}

export type ProviderStatusValue = "connected" | "failed" | "not_configured" | "not_verified";

export type IntegrationProvider = "openai" | "elevenlabs" | "exotel" | "email";

export interface ProviderStatus {
  provider: string;
  configured: boolean;
  status: ProviderStatusValue;
  detail: string;
  last_tested_at: string | null;
}

export interface SystemStatus {
  environment: string;
  app_version: string;
  timestamp: string;
  database: string;
  providers: ProviderStatus[];
}

// Result of an explicit POST /system/integrations/{provider}/test call.
// "not_configured" here can only mean the provider had no real credential
// to test in the first place — never "connected".
export interface IntegrationTestResult {
  provider: string;
  status: "connected" | "failed" | "not_configured";
  message: string;
  tested_at: string;
}

export interface LoginRequest {
  email: string;
  password: string;
}

export interface TokenResponse {
  access_token: string;
  refresh_token: string;
  token_type: string;
}
