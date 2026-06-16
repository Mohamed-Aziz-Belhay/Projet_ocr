import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';
import { AuthApiService } from './auth-api.service';

export interface AssistantChatResponse {
  reply: string;
  suggestions?: string[];
  severity?: string;
  mode?: string;
  debug?: any;
}

@Injectable({
  providedIn: 'root',
})
export class AssistantApiService {
  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');

  constructor(private http: HttpClient, private auth: AuthApiService) {}

  chat(
    message: string,
    lastResult: any | null,
    useAi: boolean,
    includeHistory: boolean
  ): Observable<AssistantChatResponse> {
    return this.http.post<AssistantChatResponse>(
      `${this.baseUrl}/assistant/chat`,
      {
        message,
        last_result: lastResult,
        use_ai: Boolean(useAi),
        include_history: Boolean(includeHistory),
      },
      {
        headers: this.auth.authHeaders(),
      }
    );
  }

  status(): Observable<any> {
    return this.http.get<any>(
      `${this.baseUrl}/assistant/status`,
      {
        headers: this.auth.authHeaders(),
      }
    );
  }

  getLastResult(): any | null {
    const raw = localStorage.getItem('ocr_last_result');

    if (!raw) {
      return null;
    }

    try {
      return JSON.parse(raw);
    } catch {
      return null;
    }
  }

  hasLastResult(): boolean {
    return this.getLastResult() !== null;
  }

  clearLastResult(): void {
    localStorage.removeItem('ocr_last_result');
  }
}