import { CommonModule } from '@angular/common';
import { Component, OnInit, computed, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AdminApiService, UserCreatePayload, UserUpdatePayload } from '../../../services/admin-api.service';
import { AuthApiService, AuthUser } from '../../../services/auth-api.service';

@Component({
  selector: 'ocr-admin-users',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './admin-users.component.html',
  styleUrl: './admin-users.component.css',
})
export class AdminUsersComponent implements OnInit {
  users       = signal<AuthUser[]>([]);
  loading     = signal(false);
  error       = signal<string | null>(null);
  message     = signal<string | null>(null);
  searchQuery = signal<string>('');

  filteredUsers = computed(() => {
    const q = this.searchQuery().trim().toLowerCase();
    if (!q) return this.users();
    return this.users().filter(u =>
      [u.email, u.full_name, u.role, u.id]
        .filter(Boolean).join(' ').toLowerCase().includes(q)
    );
  });

  createForm: UserCreatePayload = {
    email: '', password: '', full_name: '', role: 'operator', is_superuser: false,
  };
  editUserId = signal<string | null>(null);
  editForm: UserUpdatePayload = {};

  constructor(private adminApi: AdminApiService, public auth: AuthApiService) {}

  ngOnInit(): void { this.load(); }

  load(): void {
    this.loading.set(true);
    this.error.set(null);
    this.adminApi.listUsers().subscribe({
      next:  r => { this.users.set(r.items || []); this.loading.set(false); },
      error: e => { this.error.set(e?.error?.detail || e?.message || 'Chargement impossible.'); this.loading.set(false); },
    });
  }

  clearSearch(): void { this.searchQuery.set(''); }

  createUser(): void {
    this.error.set(null); this.message.set(null);
    if (!this.createForm.email.trim())                          { this.error.set('Email obligatoire.'); return; }
    if (!this.createForm.password || this.createForm.password.length < 8) { this.error.set('Mot de passe minimum 8 caractères.'); return; }
    this.adminApi.createUser(this.createForm).subscribe({
      next:  () => { this.message.set('Utilisateur créé.'); this.createForm = { email: '', password: '', full_name: '', role: 'operator', is_superuser: false }; this.load(); },
      error: e  => this.error.set(e?.error?.detail || e?.message || 'Création impossible.'),
    });
  }

  startEdit(user: AuthUser): void {
    this.editUserId.set(user.id);
    this.editForm = { full_name: user.full_name || '', role: user.role as any, is_active: user.is_active, is_superuser: user.is_superuser };
  }

  cancelEdit(): void { this.editUserId.set(null); this.editForm = {}; }

  saveEdit(user: AuthUser): void {
    this.adminApi.updateUser(user.id, this.editForm).subscribe({
      next:  () => { this.message.set('Utilisateur modifié.'); this.cancelEdit(); this.load(); },
      error: e  => this.error.set(e?.error?.detail || e?.message || 'Modification impossible.'),
    });
  }

  disable(user: AuthUser): void {
    if (!confirm(`Désactiver ${user.email} ?`)) return;
    this.adminApi.disableUser(user.id).subscribe({
      next:  () => { this.message.set('Utilisateur désactivé.'); this.load(); },
      error: e  => this.error.set(e?.error?.detail || e?.message || 'Désactivation impossible.'),
    });
  }
}