import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { AuthApiService, RegisterChoice } from '../../services/auth-api.service';

@Component({
  selector: 'ocr-register',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './register.component.html',
  styleUrl: './register.component.css',
})
export class RegisterComponent {
  fullName = '';
  email = '';
  password = '';
  confirmPassword = '';
  apiKey = localStorage.getItem('ocr_api_key') || 'dev-key-123';

  requestedAccountType: RegisterChoice = 'simple_user';

  loading = false;
  message = '';
  error = false;
  showPassword = false;
  showConfirmPassword = false;
  showApiKey = false;

  constructor(private auth: AuthApiService) {}

  register(): void {
    this.message = '';
    this.error = false;

    if (!this.email.trim()) return this.setMessage('Email obligatoire.', true);
    if (!this.password.trim()) return this.setMessage('Mot de passe obligatoire.', true);

    if (this.password.length < 8) {
      return this.setMessage('Le mot de passe doit contenir au moins 8 caractères.', true);
    }

    if (this.password !== this.confirmPassword) {
      return this.setMessage('Les mots de passe ne correspondent pas.', true);
    }

    this.loading = true;

    this.auth
      .register(this.email.trim(), this.password, this.fullName.trim(), this.requestedAccountType)
      .subscribe({
        next: response => {
          this.loading = false;

          if (this.apiKey.trim()) {
            localStorage.setItem('ocr_api_key', this.apiKey.trim());
          }

          this.password = '';
          this.confirmPassword = '';

          if (this.requestedAccountType === 'operator') {
            this.auth.logout();
            this.setMessage(
              "Votre demande de compte opérateur a été envoyée. Vous devez attendre la validation de l'admin.",
              false
            );
            return;
          }

          this.setMessage(
            `Compte simple user créé : ${response.user.email}. Vous pouvez maintenant lancer des extractions simples.`,
            false
          );
        },
        error: err => {
          this.loading = false;
          const detail =
            err?.error?.detail ||
            err?.error?.message ||
            err?.message ||
            'Création de compte impossible.';

          this.setMessage(String(detail), true);
        },
      });
  }

  togglePassword(): void { this.showPassword = !this.showPassword; }
  toggleConfirmPassword(): void { this.showConfirmPassword = !this.showConfirmPassword; }
  toggleApiKey(): void { this.showApiKey = !this.showApiKey; }

  private setMessage(value: string, isError: boolean): void {
    this.message = value;
    this.error = isError;
  }
}
