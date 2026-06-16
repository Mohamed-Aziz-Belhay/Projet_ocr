import { HttpClient, HttpHeaders , HttpParams} from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthApiService, AuthUser, UserRole } from './auth-api.service';


export interface UserListResponse { items: AuthUser[]; }

export interface UserCreatePayload {
  email: string;
  password: string;
  full_name?: string | null;
  role: UserRole;
  is_superuser?: boolean;
  is_active?: boolean;
}

export interface UserUpdatePayload {
  full_name?: string | null;
  role?: UserRole;
  is_active?: boolean;
  is_superuser?: boolean;
}

export interface DashboardStats {
  scope: string;
  users: { total: number; active: number; admins: number };
  extractions: {
    total: number;
    success: number;
    review_required: number;
    failed: number;
    avg_confidence?: number | null;
    avg_processing_time_ms?: number | null;
    by_document_type: Array<{ document_type: string; count: number }>;
    recent: any[];
    error?: string;
  };
}

@Injectable({ providedIn: 'root' })
export class AdminApiService {
  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');

  constructor(private http: HttpClient, private auth: AuthApiService) {}

  listUsers(): Observable<UserListResponse> {
    return this.http.get<UserListResponse>(`${this.baseUrl}/users`, { headers: this.headers() });
  }

  listPendingOperators(): Observable<UserListResponse> {
    return this.http.get<UserListResponse>(`${this.baseUrl}/users/pending`, { headers: this.headers() });
  }

  createUser(payload: UserCreatePayload): Observable<AuthUser> {
    return this.http.post<AuthUser>(`${this.baseUrl}/users`, payload, { headers: this.headers() });
  }

  updateUser(id: string, payload: UserUpdatePayload): Observable<AuthUser> {
    return this.http.patch<AuthUser>(`${this.baseUrl}/users/${encodeURIComponent(id)}`, payload, {
      headers: this.headers(),
    });
  }

  approveUser(id: string): Observable<AuthUser> {
    return this.http.post<AuthUser>(`${this.baseUrl}/users/${encodeURIComponent(id)}/approve`, {}, {
      headers: this.headers(),
    });
  }

  disableUser(id: string): Observable<{ ok: boolean; id: string; is_active: boolean }> {
    return this.http.delete<{ ok: boolean; id: string; is_active: boolean }>(
      `${this.baseUrl}/users/${encodeURIComponent(id)}`,
      { headers: this.headers() }
    );
  }

  dashboardStats(params?: { date_from?: string; date_to?: string }) {
  let httpParams = new HttpParams();

  if (params?.date_from) {
    httpParams = httpParams.set('date_from', params.date_from);
  }

  if (params?.date_to) {
    httpParams = httpParams.set('date_to', params.date_to);
  }

  return this.http.get<DashboardStats>(
    `${this.baseUrl}/dashboard/stats`,
    {
      headers: this.auth.authHeaders(),
      params: httpParams,
    }
  );
}

  private headers(): HttpHeaders {
    return this.auth.authHeaders();
  }
}
