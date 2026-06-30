import { Routes } from '@angular/router';

import { ExtractionComponent } from './pages/extraction/extraction.component';
import { TemplatesComponent } from './pages/templates/templates.component';
import { LoginComponent } from './pages/login/login.component';
import { RegisterComponent } from './pages/register/register.component';
import { ProfileComponent } from './pages/profile/profile.component';
import { HistoryComponent } from './pages/history/history.component';
import { AdminDashboardComponent } from './pages/admin/dashboard/admin-dashboard.component';
import { AdminUsersComponent } from './pages/admin/users/admin-users.component';
import { AdminSwinComponent } from './pages/admin/swin/admin-swin.component';
import { OcrAssistantComponent } from './pages/assistant/ocr-assistant.component';
import { MonitoringComponent } from './pages/admin/monitoring/monitoring.component';

import { authGuard } from './services/auth.guard';
import { adminGuard } from './services/admin.guard';
import { roleGuard } from './services/role.guard';
import { HomeComponent } from './pages/home/home.component';

export const routes: Routes = [
  { path: '', component: HomeComponent },

  { path: 'login', component: LoginComponent },
  { path: 'register', component: RegisterComponent },

  {
  path: 'forgot-password',
  loadComponent: () =>
    import('./pages/forgot-password/forgot-password.component')
      .then(m => m.ForgotPasswordComponent),
  },
  { path: 'templates', component: TemplatesComponent, canActivate: [authGuard, adminGuard] },
  { path: 'admin/dashboard', component: AdminDashboardComponent, canActivate: [authGuard, adminGuard] },
  { path: 'admin/users', component: AdminUsersComponent, canActivate: [authGuard, adminGuard] },
  { path: 'admin/swin', component: AdminSwinComponent, canActivate: [authGuard, adminGuard] },

  { path: 'history', component: HistoryComponent, canActivate: [roleGuard], data: { roles: ['admin', 'operator','simple_user'] } },
  { path: 'assistant', component: OcrAssistantComponent, canActivate: [authGuard, roleGuard], data: { roles: ['admin', 'operator'] } },

  { path: 'extract', component: ExtractionComponent, canActivate: [authGuard] },
  { path: 'profile', component: ProfileComponent, canActivate: [authGuard] },
  {
    path: 'admin/monitoring',
    component: MonitoringComponent,
    canActivate: [authGuard, adminGuard],
  },

  { path: '**', redirectTo: '' },
  
];
