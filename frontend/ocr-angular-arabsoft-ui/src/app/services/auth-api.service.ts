import { HttpClient, HttpHeaders } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, tap } from 'rxjs';
import { environment } from '../../environments/environment';

export type UserRole = 'admin' | 'operator' | 'simple_user' | 'viewer';
export type RegisterChoice = 'simple_user' | 'operator';

export interface AuthUser {
  id: string;
  email: string;
  full_name?: string | null;
  role: UserRole | string;
  is_active: boolean;
  is_superuser: boolean;
  organization_id?: string | null;
  last_login_at?: string | null;
}

export interface LoginResponse {
  access_token: string;
  token_type: string;
  expires_in: number;
  user: AuthUser;
  api_key?: string;   // ✅ Clé API retournée par le backend au login
}

export interface HistoryItem {
  id: string;
  job_id?: string | null;
  user_id?: string | null;
  user_email?: string | null;
  user_role?: string | null;
  organization_id?: string | null;
  file_name?: string | null;
  document_type?: string | null;
  template_id?: string | null;
  engine_used?: string | null;
  status?: string | null;
  global_confidence?: number | null;
  processing_time_ms?: number | null;
  field_count?: number | null;
  created_at?: string | null;
}

export interface HistoryResponse {
  items: HistoryItem[];
}

export interface HistoryDetailResponse {
  history: HistoryItem;
  raw_text?: string | null;
  result_json?: any;
  fields_json?: any;
  diagnostics_json?: any;
  created_at?: string | null;
}

@Injectable({ providedIn: 'root' })
export class AuthApiService {
  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');

  constructor(private http: HttpClient) {}

  login(email: string, password: string): Observable<LoginResponse> {
    return this.http
      .post<LoginResponse>(`${this.baseUrl}/auth/login`, { email, password })
      .pipe(tap(response => this.saveSession(response)));
  }

  register(
    email: string,
    password: string,
    fullName?: string,
    choice: RegisterChoice = 'simple_user'
  ): Observable<LoginResponse> {
    const role: UserRole = choice === 'operator' ? 'operator' : 'simple_user';

    return this.http
      .post<LoginResponse>(`${this.baseUrl}/auth/register`, {
        email,
        password,
        full_name: fullName || null,
        role,
        requested_role: role,
        requires_admin_approval: role === 'operator',
      })
      .pipe(
        tap(response => {
          if (response?.access_token) {
            this.saveSession(response);
          }
        })
      );
  }

  me(): Observable<AuthUser> {
    return this.http.get<AuthUser>(`${this.baseUrl}/auth/me`, {
      headers: this.authHeaders(),
    });
  }

  changePassword(currentPassword: string, newPassword: string): Observable<any> {
    return this.http.post<any>(
      `${this.baseUrl}/auth/change-password`,
      {
        current_password: currentPassword,
        new_password: newPassword,
      },
      { headers: this.authHeaders() }
    );
  }

  requestPasswordReset(email: string): Observable<any> {
    return this.http.post<any>(`${this.baseUrl}/auth/forgot-password`, { email });
  }

  resetPasswordWithCode(email: string, code: string, newPassword: string): Observable<any> {
    return this.http.post<any>(`${this.baseUrl}/auth/reset-password`, {
      email,
      code,
      new_password: newPassword,
    });
  }

  history(limit = 50): Observable<HistoryResponse> {
    return this.http.get<HistoryResponse>(
      `${this.baseUrl}/history?limit=${limit}`,
      { headers: this.authHeaders() }
    );
  }

  historyDetail(idOrJobId: string): Observable<HistoryDetailResponse> {
    return this.http.get<HistoryDetailResponse>(
      `${this.baseUrl}/history/${encodeURIComponent(idOrJobId)}`,
      { headers: this.authHeaders() }
    );
  }

  logout(): void {
    localStorage.removeItem('ocr_access_token');
    localStorage.removeItem('ocr_user');
    localStorage.removeItem('ocr_user_email');
    localStorage.removeItem('ocr_api_key');   // ✅ Nettoyer la clé API aussi
  }

  saveLoginResponse(response: LoginResponse): void {
    this.saveSession(response);
  }

  getToken(): string {
    return localStorage.getItem('ocr_access_token') || '';
  }

  // ✅ Plus de fallback 'dev-key-123' — retourne '' si absente
  getApiKey(): string {
    return localStorage.getItem('ocr_api_key') || '';
  }

  getUser(): AuthUser | null {
    const raw = localStorage.getItem('ocr_user');
    if (!raw) return null;
    try {
      return JSON.parse(raw) as AuthUser;
    } catch {
      return null;
    }
  }

  getRole(): UserRole | string {
    return this.getUser()?.role || '';
  }

  isLoggedIn(): boolean {
    return Boolean(this.getToken());
  }

  isAdmin(): boolean {
    const user = this.getUser();
    return Boolean(user?.is_superuser || user?.role === 'admin');
  }

  isOperator(): boolean   { return this.getRole() === 'operator'; }
  isSimpleUser(): boolean { return this.getRole() === 'simple_user'; }
  isViewer(): boolean     { return this.getRole() === 'viewer'; }

  hasRole(roles: Array<UserRole | 'superuser'>): boolean {
    const user = this.getUser();
    if (!user) return false;
    if (roles.includes('superuser') && user.is_superuser) return true;
    if (user.is_superuser) return true;
    return roles.includes(user.role as UserRole);
  }

  canAdmin():            boolean { return this.isAdmin(); }
  canManageUsers():      boolean { return this.isAdmin(); }
  canManageTemplates():  boolean { return this.isAdmin(); }
  canManageSwin():       boolean { return this.isAdmin(); }
  canSeeDashboard():     boolean { return this.isAdmin(); }
  canExtract():          boolean { return this.hasRole(['admin', 'operator', 'simple_user']); }
  canExport():           boolean { return this.hasRole(['admin', 'operator', 'simple_user']); }
  canUseAssistant():     boolean { return this.hasRole(['admin', 'operator']); }
  canViewHistory():      boolean { return this.hasRole(['admin', 'operator', 'simple_user']); }
  canViewProfile():      boolean { return this.hasRole(['admin', 'operator', 'simple_user', 'viewer']); }

  authHeaders(): HttpHeaders {
    let headers = new HttpHeaders();
    const token  = this.getToken();
    const apiKey = this.getApiKey();
    if (token)  headers = headers.set('Authorization', `Bearer ${token}`);
    if (apiKey) headers = headers.set('X-API-Key', apiKey);
    return headers;
  }

  // ✅ Sauvegarde la clé API retournée par le backend
  private saveSession(response: LoginResponse): void {
    localStorage.setItem('ocr_access_token', response.access_token);
    localStorage.setItem('ocr_user',         JSON.stringify(response.user));
    localStorage.setItem('ocr_user_email',   response.user.email);
    if (response.api_key) {
      localStorage.setItem('ocr_api_key', response.api_key);
    }
  }
}