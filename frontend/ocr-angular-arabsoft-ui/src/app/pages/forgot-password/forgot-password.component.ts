import { CommonModule } from '@angular/common';
import { Component, OnDestroy } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AuthApiService } from '../../services/auth-api.service';

@Component({
  selector: 'ocr-forgot-password',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './forgot-password.component.html',
  styleUrl: './forgot-password.component.css',
})
export class ForgotPasswordComponent implements OnDestroy {
  email = '';
  code = '';
  newPassword = '';
  confirmPassword = '';

  codeSent = false;
  loading = false;
  message = '';
  error = false;

  remainingSeconds = 0;
  private timerId: ReturnType<typeof setInterval> | null = null;

  constructor(private auth: AuthApiService) {}

  sendCode(): void {
    this.message = '';
    this.error = false;

    if (!this.email.trim()) {
      return this.setMessage('Email obligatoire.', true);
    }

    this.loading = true;

    this.auth.requestPasswordReset(this.email.trim()).subscribe({
      next: () => {
        this.loading = false;
        this.codeSent = true;
        this.startExpirationTimer();
        this.setMessage(
          'Un code de vérification a été envoyé à votre adresse email. Il expire dans 30 minutes.',
          false
        );
      },
      error: err => {
        this.loading = false;
        const detail =
          err?.error?.detail ||
          err?.error?.message ||
          err?.message ||
          "Impossible d'envoyer le code de vérification."; // ← guillemets droits
        this.setMessage(String(detail), true);
      },
    });
  }

  resetPassword(): void {
    this.message = '';
    this.error = false;

    if (!this.email.trim()) {
      return this.setMessage('Email obligatoire.', true);
    }

    if (!this.code.trim()) {
      return this.setMessage('Code de vérification obligatoire.', true);
    }

    if (!this.newPassword.trim()) {
      return this.setMessage('Nouveau mot de passe obligatoire.', true);
    }

    if (this.newPassword.length < 8) {
      return this.setMessage('Le mot de passe doit contenir au moins 8 caractères.', true);
    }

    const passwordBytes = new TextEncoder().encode(this.newPassword).length;

    if (passwordBytes > 72) {
      return this.setMessage(
        'Le mot de passe est trop long. Maximum 72 octets avec bcrypt.',
        true
      );
    }

    if (this.newPassword !== this.confirmPassword) {
      return this.setMessage('Les deux mots de passe ne correspondent pas.', true);
    }

    this.loading = true;

    this.auth
      .resetPasswordWithCode(
        this.email.trim(),
        this.code.trim(),
        this.newPassword.trim() // ← trim ajouté
      )
      .subscribe({
        next: () => {
          this.loading = false;
          this.clearTimer();
          this.codeSent = false; // ← réinitialise l'affichage du formulaire
          this.setMessage(
            'Mot de passe modifié avec succès. Vous pouvez maintenant vous connecter.',
            false
          );

          this.code = '';
          this.newPassword = '';
          this.confirmPassword = '';
        },
        error: err => {
          this.loading = false;
          const detail =
            err?.error?.detail ||
            err?.error?.message ||
            err?.message ||
            'Code invalide ou expiré.';
          this.setMessage(String(detail), true);
        },
      });
  }

  resendCode(): void {
    this.clearTimer();
    this.code = '';
    this.newPassword = '';
    this.confirmPassword = '';
    this.codeSent = false;
    this.sendCode();
  }

  get remainingTime(): string {
    const minutes = Math.floor(this.remainingSeconds / 60);
    const seconds = this.remainingSeconds % 60;
    return `${minutes.toString().padStart(2, '0')}:${seconds
      .toString()
      .padStart(2, '0')}`;
  }

  private startExpirationTimer(): void {
    this.clearTimer();
    this.remainingSeconds = 30 * 60;

    this.timerId = setInterval(() => {
      this.remainingSeconds--;

      if (this.remainingSeconds <= 0) {
        this.clearTimer();
        this.codeSent = false;
        this.setMessage(
          'Le code de vérification a expiré. Veuillez demander un nouveau code.',
          true
        );
      }
    }, 1000);
  }

  private clearTimer(): void {
    if (this.timerId) {
      clearInterval(this.timerId);
      this.timerId = null;
    }
  }

  private setMessage(value: string, isError: boolean): void {
    this.message = value;
    this.error = isError;
  }

  ngOnDestroy(): void {
    this.clearTimer();
  }
}