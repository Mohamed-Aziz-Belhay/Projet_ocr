import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthApiService } from './auth-api.service';

export interface ExportPayload {
  file_name?: string | null;
  document_type?: string | null;
  template_id?: string | null;
  result: any;
  metadata?: Record<string, any>;
}

@Injectable({
  providedIn: 'root',
})
export class ExportApiService {
  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');

  constructor(private http: HttpClient, private auth: AuthApiService) {}

  exportPdf(payload: ExportPayload): Observable<Blob> {
    return this.http.post(`${this.baseUrl}/exports/pdf`, payload, {
      headers: this.auth.authHeaders(),
      responseType: 'blob',
    });
  }

  downloadJson(result: any, filename = 'ocr_result.json'): void {
    const blob = new Blob([JSON.stringify(result || {}, null, 2)], {
      type: 'application/json;charset=utf-8',
    });

    this.downloadBlob(blob, filename);
  }

  downloadCsv(result: any, filename = 'ocr_result.csv'): void {
    const rows = this.resultToRows(result);
    const csv = this.rowsToCsv(rows);
    const blob = new Blob(['\ufeff' + csv], {
      type: 'text/csv;charset=utf-8',
    });

    this.downloadBlob(blob, filename);
  }

  downloadBlob(blob: Blob, filename: string): void {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');

    a.href = url;
    a.download = filename;
    a.click();

    URL.revokeObjectURL(url);
  }

  private resultToRows(result: any): Array<Record<string, any>> {
    const normalized = result?.normalized_data || result?.normalizedData || {};
    const fields = Array.isArray(result?.fields) ? result.fields : [];

    const rows: Array<Record<string, any>> = [];

    if (fields.length) {
      for (const field of fields) {
        rows.push({
          section: 'fields',
          key: field?.name || field?.field_name || field?.key || '',
          value: this.stringifyValue(field?.value),
          confidence: field?.confidence ?? '',
          source: field?.selected_source || field?.selected_engine || '',
          validated: field?.validated ?? '',
        });
      }
    }

    for (const [key, value] of Object.entries(normalized)) {
      if (Array.isArray(value)) {
        value.forEach((item, index) => {
          rows.push({
            section: key,
            key: `${key}[${index}]`,
            value: this.stringifyValue(item),
            confidence: '',
            source: 'normalized_data',
            validated: '',
          });
        });
      } else {
        rows.push({
          section: 'normalized_data',
          key,
          value: this.stringifyValue(value),
          confidence: '',
          source: 'normalized_data',
          validated: '',
        });
      }
    }

    if (!rows.length && result && typeof result === 'object') {
      for (const [key, value] of Object.entries(result)) {
        if (typeof value !== 'object') {
          rows.push({
            section: 'result',
            key,
            value: this.stringifyValue(value),
            confidence: '',
            source: 'result',
            validated: '',
          });
        }
      }
    }

    return rows.length
      ? rows
      : [{ section: 'empty', key: 'result', value: '', confidence: '', source: '', validated: '' }];
  }

  private rowsToCsv(rows: Array<Record<string, any>>): string {
    const headers = ['section', 'key', 'value', 'confidence', 'source', 'validated'];
    const lines = [headers.join(',')];

    for (const row of rows) {
      lines.push(headers.map(h => this.csvCell(row[h])).join(','));
    }

    return lines.join('\n');
  }

  private csvCell(value: any): string {
    const text = value === null || value === undefined ? '' : String(value);
    return `"${text.replace(/"/g, '""')}"`;
  }

  private stringifyValue(value: any): string {
    if (value === null || value === undefined) return '';
    if (typeof value === 'object') return JSON.stringify(value, null, 0);
    return String(value);
  }
}
