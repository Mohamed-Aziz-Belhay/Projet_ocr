import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AuthApiService } from '../../services/auth-api.service';
import { RouterLink } from '@angular/router';

@Component({
  selector: 'ocr-login',
  standalone: true,
  imports: [CommonModule, FormsModule,RouterLink],
  templateUrl: './login.component.html',
  styleUrl: './login.component.css',
})
export class LoginComponent {
  email = localStorage.getItem('ocr_user_email') || '';
  password = '';
  apiKey = localStorage.getItem('ocr_api_key') || 'dev-key-123';
  remember = true;
  loading = false;
  message = '';
  error = false;
  showPassword = false;

  constructor(private auth: AuthApiService) {}


  login(): void {
    this.message = '';
    this.error = false;

    if (!this.email.trim()) {
      return this.setMessage('Email obligatoire.', true);
    }

    if (!this.password.trim()) {
      return this.setMessage('Mot de passe obligatoire.', true);
    }

    this.loading = true;

    this.auth.login(this.email.trim(), this.password).subscribe({
      next: response => {
        this.loading = false;

        if (this.remember && this.apiKey.trim()) {
          localStorage.setItem('ocr_api_key', this.apiKey.trim());
        } else if (!this.remember) {
          localStorage.removeItem('ocr_api_key');
        }

        this.password = '';

        this.setMessage(
          `Connexion réussie : ${response.user.email} (${response.user.role}).`,
          false
        );
      },
      error: err => {
        this.loading = false;

        const detail =
          err?.error?.detail ||
          err?.error?.message ||
          err?.message ||
          'Échec de connexion.';

        this.setMessage(String(detail), true);
      },
    });
  }

  togglePassword(): void {
    this.showPassword = !this.showPassword;
  }

  clear(): void {
    this.email = '';
    this.password = '';
    this.apiKey = '';
    this.message = '';
    this.error = false;

    this.auth.logout();
    localStorage.removeItem('ocr_api_key');
  }

  logout(): void {
    this.auth.logout();
    this.password = '';
    this.setMessage('Session supprimée.', false);
  }

  get currentUserEmail(): string {
    return this.auth.getUser()?.email || '';
  }

  get isLoggedIn(): boolean {
    return this.auth.isLoggedIn();
  }

  private setMessage(value: string, isError: boolean): void {
    this.message = value;
    this.error = isError;
  }
}
