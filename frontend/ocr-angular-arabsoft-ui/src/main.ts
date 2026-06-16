import { bootstrapApplication } from '@angular/platform-browser';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideRouter, Routes } from '@angular/router';

import { AppComponent } from './app/app.component';

import { ExtractionComponent } from './app/pages/extraction/extraction.component';
import { TemplatesComponent } from './app/pages/templates/templates.component';
import { LoginComponent } from './app/pages/login/login.component';
import { RegisterComponent } from './app/pages/register/register.component';
import { ProfileComponent } from './app/pages/profile/profile.component';
import { HistoryComponent } from './app/pages/history/history.component';
import { AdminDashboardComponent } from './app/pages/admin/dashboard/admin-dashboard.component';
import { AdminUsersComponent } from './app/pages/admin/users/admin-users.component';
import { OcrAssistantComponent } from './app/pages/assistant/ocr-assistant.component';

import { apiKeyInterceptor } from './app/services/api-key.interceptor';
import { authGuard } from './app/services/auth.guard';
import { adminGuard } from './app/services/admin.guard';
import { roleGuard } from './app/services/role.guard';
import { ForgotPasswordComponent } from './app/pages/forgot-password/forgot-password.component';

const routes: Routes = [
  // Page publique : visible même sans login.
  // Le bouton "lancer extraction" reste protégé dans extraction.component.ts.
  { path: '', component: ExtractionComponent },

  // Admin seulement
  {
    path: 'templates',
    component: TemplatesComponent,
    canActivate: [authGuard, adminGuard],
  },
  {
    path: 'admin/dashboard',
    component: AdminDashboardComponent,
    canActivate: [authGuard, adminGuard],
  },
  {
    path: 'admin/users',
    component: AdminUsersComponent,
    canActivate: [authGuard, adminGuard],
  },

  // Admin + operator seulement
  {
    path: 'history',
    component: HistoryComponent,
    canActivate: [authGuard, roleGuard],
    data: { roles: ['admin', 'operator','simple_user'] },
  },
  {
    path: 'assistant',
    component: OcrAssistantComponent,
    canActivate: [authGuard, roleGuard],
    data: { roles: ['admin', 'operator'] },
  },

  // Tout utilisateur connecté
  {
    path: 'profile',
    component: ProfileComponent,
    canActivate: [authGuard],
  },

  { path: 'login', component: LoginComponent },
  { path: 'forgot-password', component: ForgotPasswordComponent },
  { path: 'register', component: RegisterComponent },

  { path: '**', redirectTo: '' },
];

bootstrapApplication(AppComponent, {
  providers: [
    provideRouter(routes),
    provideHttpClient(withInterceptors([apiKeyInterceptor])),
  ],
}).catch(console.error);



