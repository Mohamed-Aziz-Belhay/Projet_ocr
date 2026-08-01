// frontend/ocr-angular-arabsoft-ui/src/app/services/ocr-api.service.ts
import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthApiService } from './auth-api.service';

// ── Interfaces alignées sur to_summary() PostgreSQL ──────────────────────────
export interface TemplateSummary {
  id: string;
  name: string;
  document_type: string | null;
  doc_family: string | null;
  language: string | null;
  preferred_engine: string;
  pipeline: string;
  template_mode: string;
  is_active: boolean;
  field_count: number;
  usage_count: number;
  created_at?: string;
  updated_at?: string;
}

export interface TemplateDetail {
  id: string;
  name: string;
  version: string;
  description: string | null;
  document_type: string | null;
  doc_family: string | null;
  language: string | null;
  preferred_engine: string;
  pipeline: string;
  template_mode: string;
  is_active: boolean;
  fields: any[];
  output_mapping: Record<string, string>;
  language_hints: string[];
  anchors_required: string[];
  postprocess_hooks: string[];
  fixed_zones: Record<string, any>;
  engines: Record<string, any>;
  field_policies: Record<string, any>;
  review_policy: Record<string, any>;
  usage_count: number;
  extra: Record<string, any>;
  created_at?: string;
  updated_at?: string;
}

export interface ExtractRequest {
  file: File;
  documentType: string;
  templateId?: string;
  engine: string;
  processingMode: string;
  cinMode: string;
  languageHint?: string;
  includeDiagnostics: boolean;
}

@Injectable({ providedIn: 'root' })
export class OcrApiService {

  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');

  constructor(private http: HttpClient, private authService: AuthApiService) {}

  // ── Extraction ────────────────────────────────────────────────────────────
  extract(req: ExtractRequest): Observable<any> {
      const f = new FormData();
      f.append('file',               req.file);
      f.append('document_type',      req.documentType);
      f.append('engine',             req.engine);
      f.append('processing_mode',    req.processingMode);
      f.append('cin_mode',           req.cinMode);
      f.append('include_diagnostics', String(req.includeDiagnostics));
      if (req.templateId)   f.append('template_id',   req.templateId);
      if (req.languageHint) f.append('language_hint', req.languageHint);

      return this.http.post<any>(
        `${this.baseUrl}/extract`, f,
        { headers: this.authService.authHeaders() }
    );
  }

  // ── Templates ─────────────────────────────────────────────────────────────
  // [SÉCURITÉ #4] Les 4 méthodes ci-dessous n'envoyaient auparavant aucun
  // header Authorization : PUT/DELETE, protégés côté backend par
  // get_current_admin_user (JWT obligatoire), échouaient donc toujours en
  // 401 dès qu'un compte admin tentait de créer/modifier/supprimer un
  // template — l'intercepteur global traduisait ce 401 en faux message
  // "session expirée". GET/GET détail restaient fonctionnels côté backend
  // (RG8 : consultation ouverte à tous les rôles) mais sont alignés ici
  // par cohérence.

  /**
   * GET /templates
   * Retourne la liste pour le catalogue Angular.
   */
  listTemplates(params?: {
    search?: string;
    document_type?: string;
    is_active?: boolean;
    skip?: number;
    limit?: number;
  }): Observable<TemplateSummary[]> {
    let p = new HttpParams();
    if (params?.search)                p = p.set('search',        params.search);
    if (params?.document_type)         p = p.set('document_type', params.document_type);
    if (params?.is_active !== undefined) p = p.set('is_active',   String(params.is_active));
    if (params?.skip  !== undefined)   p = p.set('skip',          String(params.skip));
    if (params?.limit !== undefined)   p = p.set('limit',         String(params.limit));

    return this.http.get<TemplateSummary[]>(
      `${this.baseUrl}/templates`,
      { params: p, headers: this.authService.authHeaders() },
    );
  }

  /**
   * GET /templates/{id}
   * Charge le détail complet dans l'éditeur.
   */
  getTemplate(id: string): Observable<TemplateDetail> {
    return this.http.get<TemplateDetail>(
      `${this.baseUrl}/templates/${encodeURIComponent(id)}`,
      { headers: this.authService.authHeaders() },
    );
  }

  /**
   * PUT /templates/{id}
   * Upsert (création si absent, mise à jour sinon).
   * Le backend (TemplateBody) attend :
   *   { "id": "...", "name": "...", "fields": [...], ... }
   * Nécessite un JWT admin (get_current_admin_user, RG8).
   */
  saveTemplate(id: string, content: any): Observable<TemplateDetail> {
    const body = { ...content, id };
    return this.http.put<TemplateDetail>(
      `${this.baseUrl}/templates/${encodeURIComponent(id)}`,
      body,
      { headers: this.authService.authHeaders() },
    );
  }

  /**
   * DELETE /templates/{id}
   * Nécessite un JWT admin (get_current_admin_user, RG8).
   */
  deleteTemplate(id: string): Observable<void> {
    return this.http.delete<void>(
      `${this.baseUrl}/templates/${encodeURIComponent(id)}`,
      { headers: this.authService.authHeaders() },
    );
  }
}