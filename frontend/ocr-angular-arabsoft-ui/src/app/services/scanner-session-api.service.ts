import { HttpClient } from '@angular/common/http';
import { Injectable } from '@angular/core';
import { Observable, of } from 'rxjs';
import { catchError } from 'rxjs/operators';
import { environment } from '../../environments/environment';
import { AuthApiService } from './auth-api.service';

@Injectable({
  providedIn: 'root',
})
export class ScannerSessionApiService {
  private readonly baseUrl = environment.apiBaseUrl.replace(/\/$/, '');

  constructor(private http: HttpClient, private auth: AuthApiService) {}

  claim(): Observable<any> {
    if (!this.auth.isLoggedIn()) {
      return of(null);
    }

    return this.http
      .post<any>(
        `${this.baseUrl}/scanner/session/claim`,
        {},
        { headers: this.auth.authHeaders() }
      )
      .pipe(
        catchError(err => {
          console.warn('Scanner session claim failed', err);
          return of(null);
        })
      );
  }

  current(): Observable<any> {
    return this.http.get<any>(
      `${this.baseUrl}/scanner/session/current`,
      { headers: this.auth.authHeaders() }
    );
  }

  release(): Observable<any> {
    return this.http
      .post<any>(
        `${this.baseUrl}/scanner/session/release`,
        {},
        { headers: this.auth.authHeaders() }
      )
      .pipe(
        catchError(err => {
          console.warn('Scanner session release failed', err);
          return of(null);
        })
      );
  }
}