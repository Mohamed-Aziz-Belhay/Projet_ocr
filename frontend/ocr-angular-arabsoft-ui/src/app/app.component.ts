import { Component, OnDestroy, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { Router, RouterLink, RouterOutlet } from '@angular/router';
import { AuthApiService } from './services/auth-api.service';
import { ScannerSessionApiService } from './services/scanner-session-api.service';

type ThemeMode = 'dark' | 'light';

@Component({
  selector: 'ocr-root',
  standalone: true,
  imports: [CommonModule, RouterOutlet, RouterLink],
  templateUrl: './app.component.html',
  styleUrl: './app.component.css',
})
export class AppComponent implements OnInit, OnDestroy {
  theme: ThemeMode = 'dark';
  private scannerClaimTimer: any = null;

  constructor(
    public auth: AuthApiService,
    private router: Router,
    private scannerSession: ScannerSessionApiService
  ) {
    const saved = localStorage.getItem('ocr_theme');
    this.theme = saved === 'light' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', this.theme);
  }

  ngOnInit(): void {
    this.claimScannerSession();

    this.scannerClaimTimer = setInterval(() => {
      this.claimScannerSession();
    }, 60_000);
  }

  ngOnDestroy(): void {
    if (this.scannerClaimTimer) {
      clearInterval(this.scannerClaimTimer);
      this.scannerClaimTimer = null;
    }
  }

  private claimScannerSession(): void {
    if (!this.auth.isLoggedIn() || !this.auth.canExtract()) {
      return;
    }

    this.scannerSession.claim().subscribe();
  }

  currentUserName(): string {
    const user = this.auth.getUser();
    if (!user) return '';
    const fullName = (user.full_name || '').trim();
    return fullName || user.email.split('@')[0] || user.email;
  }

  currentUserRole(): string {
    return this.auth.getUser()?.role || '';
  }

  logout(): void {
    this.scannerSession.release().subscribe({
      next: () => {
        this.auth.logout();
        this.router.navigate(['/login']);
      },
      error: () => {
        this.auth.logout();
        this.router.navigate(['/login']);
      },
    });
  }

  isConsoleRoute(): boolean {
    const url = this.router.url;
    return (
      url.startsWith('/admin') ||
      url.startsWith('/history') ||
      url.startsWith('/assistant') ||
      url.startsWith('/profile')
    );
  }

  toggleTheme(): void {
    this.theme = this.theme === 'dark' ? 'light' : 'dark';
    document.documentElement.setAttribute('data-theme', this.theme);
    localStorage.setItem('ocr_theme', this.theme);
  }

  isLoggedIn(): boolean {
    return this.auth.isLoggedIn();
  }

  isAdmin(): boolean {
    return this.auth.isAdmin();
  }

  isOperator(): boolean {
    return this.auth.isOperator();
  }

  isViewer(): boolean {
    return this.auth.isViewer();
  }

  canExtract(): boolean {
    return this.auth.canExtract();
  }

  canSeeDashboard(): boolean {
    return this.auth.canSeeDashboard();
  }

  canManageUsers(): boolean {
    return this.auth.canManageUsers();
  }

  canManageTemplates(): boolean {
    return this.auth.canManageTemplates();
  }

  canManageSwin(): boolean {
    return this.auth.canManageSwin();
  }

  canViewHistory(): boolean {
    return this.auth.canViewHistory();
  }

  canUseAssistant(): boolean {
    return this.auth.canUseAssistant();
  }

  canViewProfile(): boolean {
    return this.auth.canViewProfile();
  }

  isSimpleUser(): boolean {
    return this.auth.isSimpleUser();
  }
}