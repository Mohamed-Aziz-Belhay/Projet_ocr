import { CommonModule } from '@angular/common';
import { Component, OnDestroy } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AuthApiService } from '../../services/auth-api.service';

// [BUG A] Durée par défaut si jamais l'API ne retourne pas expires_in_minutes
// (compatibilité ascendante). La vraie durée doit toujours venir de la
// réponse de /forgot-password (RESET_CODE_EXPIRATION_MINUTES côté backend,
// actuellement 30 min) plutôt que d'une constante dupliquée ici.
const DEFAULT_EXPIRATION_MINUTES = 15;

// [BUG B] Le backend utilise PBKDF2-SHA256 (pas bcrypt) : la contrainte
// réelle est MAX_PASSWORD_LENGTH = 255 caractères côté serveur, pas une
// limite de 72 octets propre à bcrypt.
const MAX_PASSWORD_LENGTH = 255;

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
      next: (res: any) => {
        this.loading = false;
        this.codeSent = true;

        // [BUG A] Lire la durée réelle retournée par le backend au lieu
        // d'une valeur codée en dur, pour rester synchronisé avec
        // RESET_CODE_EXPIRATION_MINUTES même si elle change côté serveur.
        const expiresInMinutes = Number(res?.expires_in_minutes) || DEFAULT_EXPIRATION_MINUTES;
        this.startExpirationTimer(expiresInMinutes);

        this.setMessage(
          `Un code de vérification a été envoyé à votre adresse email. Il expire dans ${expiresInMinutes} minutes.`,
          false
        );
      },
      error: err => {
        this.loading = false;
        const detail =
          err?.error?.detail ||
          err?.error?.message ||
          err?.message ||
          "Impossible d'envoyer le code de vérification.";
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

    // [BUG B] Le backend (PBKDF2) compte des CARACTÈRES, pas des octets
    // UTF-8 : on vérifie donc .length directement contre la vraie limite
    // serveur (255), et non un encodage bcrypt qui ne s'applique pas ici.
    if (this.newPassword.length > MAX_PASSWORD_LENGTH) {
      return this.setMessage(
        `Le mot de passe est trop long. Maximum ${MAX_PASSWORD_LENGTH} caractères.`,
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
        this.newPassword.trim()
      )
      .subscribe({
        next: () => {
          this.loading = false;
          this.clearTimer();
          this.codeSent = false;
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

  private startExpirationTimer(expirationMinutes: number = DEFAULT_EXPIRATION_MINUTES): void {
    this.clearTimer();
    this.remainingSeconds = expirationMinutes * 60;

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