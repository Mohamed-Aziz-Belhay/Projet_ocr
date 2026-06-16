"frontend\ocr-angular-arabsoft-ui\src\app\services\api-key.interceptor.ts"
import { HttpInterceptorFn } from '@angular/common/http';
export const apiKeyInterceptor: HttpInterceptorFn = (req,next)=>{const apiKey=localStorage.getItem('ocr_api_key')||'';return apiKey?next(req.clone({setHeaders:{'X-API-Key':apiKey}})):next(req);};
