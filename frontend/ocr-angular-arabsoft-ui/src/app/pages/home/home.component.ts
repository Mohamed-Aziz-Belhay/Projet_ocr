import { CommonModule } from '@angular/common';
import { Component } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AuthApiService } from '../../services/auth-api.service';

interface StatItem {
  value: string;
  label: string;
}

interface RoleCard {
  key: string;
  name: string;
  badgeClass: string;
  items: string[];
}

@Component({
  selector: 'ocr-home',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './home.component.html',
  styleUrl: './home.component.css',
})
export class HomeComponent {
  constructor(public auth: AuthApiService) {}

  stats: StatItem[] = [
    { value: '16', label: 'Routers FastAPI' },
    { value: '4', label: 'Types de documents' },
    { value: '3', label: 'Moteurs OCR' },
    { value: '88%+', label: 'Accuracy Swin' },
  ];

  roles: RoleCard[] = [
    {
      key: 'admin',
      name: 'Admin',
      badgeClass: 'role-admin',
      items: ['Gestion des utilisateurs', 'Historique global', 'Dashboard analytique', 'Modèles Swin / YOLO'],
    },
    {
      key: 'operator',
      name: 'Operator',
      badgeClass: 'role-operator',
      items: ['Extraction', 'Templates YAML', 'Assistant OCR', 'Scanner Agent'],
    },
    {
      key: 'simple_user',
      name: 'Simple User',
      badgeClass: 'role-simple',
      items: ['Extraction', 'Son historique', 'Gestion du profil'],
    },
    {
      key: 'viewer',
      name: 'Viewer',
      badgeClass: 'role-viewer',
      items: ['Consultation uniquement', 'Pas d\u2019extraction'],
    },
  ];

  isLoggedIn(): boolean {
    return this.auth.isLoggedIn();
  }
}