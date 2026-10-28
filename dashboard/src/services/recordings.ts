import { apiRequest, QueryValue } from "@/services/api";
import type { Paginated, Recording, RecordingDetail } from "@/types/api";

export interface RecordingFilters {
  status?: string;
  department?: string;
  search?: string;
  date_from?: string;
  date_to?: string;
  limit?: number;
  offset?: number;
  [key: string]: QueryValue;
}

export function listRecordings(filters: RecordingFilters = {}): Promise<Paginated<Recording>> {
  return apiRequest<Paginated<Recording>>("/api/v1/recordings", { query: filters });
}

export function getRecording(id: string): Promise<RecordingDetail> {
  return apiRequest<RecordingDetail>(`/api/v1/recordings/${id}`);
}
