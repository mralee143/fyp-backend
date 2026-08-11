import { api } from "@/lib/api";
import type { ScanDetail } from "@/lib/detection";

export interface Me {
  email: string;
  is_admin: boolean;
}

export interface AdminStats {
  users: number;
  scans: number;
  threats: number;
}

export interface AdminUser {
  id: number;
  email: string;
  is_active: boolean;
  created_at: string;
  scan_count: number;
}

export interface AdminScan {
  id: number;
  filename: string;
  model: string;
  violence_detected: boolean;
  summary: string;
  created_at: string;
  email: string;
}

export interface AdminAnalytics {
  scans_by_day: { day: string; scans: number; threats: number }[];
  totals: { threats: number; clear: number };
  categories: { category: string; count: number }[];
  models: { model: string; count: number }[];
  top_users: { email: string; count: number }[];
}

export async function getMe(): Promise<Me> {
  const { data } = await api.get<Me>("/auth/me");
  return data;
}

export async function getAdminStats(): Promise<AdminStats> {
  const { data } = await api.get<AdminStats>("/admin/stats");
  return data;
}

export async function getAdminUsers(): Promise<AdminUser[]> {
  const { data } = await api.get<AdminUser[]>("/admin/users");
  return data;
}

export async function getAdminScans(): Promise<AdminScan[]> {
  const { data } = await api.get<AdminScan[]>("/admin/scans");
  return data;
}

export async function getAdminAnalytics(): Promise<AdminAnalytics> {
  const { data } = await api.get<AdminAnalytics>("/admin/analytics");
  return data;
}

/** Full detail of ANY user's scan (admin only). */
export async function getAdminScan(id: number): Promise<ScanDetail> {
  const { data } = await api.get<ScanDetail>(`/admin/scan/${id}`);
  return data;
}

export async function deleteUser(id: number): Promise<void> {
  await api.delete(`/admin/user/${id}`);
}

export async function deleteScan(id: number): Promise<void> {
  await api.delete(`/admin/scan/${id}`);
}
