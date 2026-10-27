import {
  ApiError,
  apiDownloadBlob,
  apiFetch,
  apiFetchBlob,
  EVENTO_SESION_CADUCADA,
  login,
  sesionCaducada,
  setStoredToken,
} from "./api";

/** La capa de llamadas anuncia la sesión caducada (un 401 en una llamada
 *  autenticada) UNA vez a toda la aplicación, y sigue lanzando el error de
 *  siempre. Los 401 de `/api/auth/*` (una contraseña mal puesta) no cuentan. */

type FetchLike = (input: string, init?: RequestInit) => Promise<unknown>;

const fetchMock = jest.fn<Promise<unknown>, Parameters<FetchLike>>();

function respuesta(status: number, body: unknown = { detail: "Could not validate credentials" }): unknown {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => null },
    json: async () => body,
    blob: async () => new Blob(["x"]),
  };
}

let avisos = 0;
const alCaducar = () => { avisos += 1; };

beforeEach(() => {
  fetchMock.mockReset();
  (globalThis as unknown as { fetch: FetchLike }).fetch = fetchMock;
  // Entrar con un token nuevo también deja el aviso listo para el próximo 401.
  setStoredToken("tok");
  avisos = 0;
  window.addEventListener(EVENTO_SESION_CADUCADA, alCaducar);
});

afterEach(() => {
  window.removeEventListener(EVENTO_SESION_CADUCADA, alCaducar);
});

test("un 401 en una llamada autenticada avisa una sola vez y sigue lanzando el ApiError", async () => {
  fetchMock.mockResolvedValue(respuesta(401));
  await expect(apiFetch("/api/erp/webs/estado")).rejects.toMatchObject({ name: "ApiError", status: 401 });
  await expect(apiFetch("/api/erp/webs/reglas")).rejects.toBeInstanceOf(ApiError);
  expect(avisos).toBe(1);
  expect(sesionCaducada()).toBe(true);
});

test("un 401 de /api/auth/login (contraseña mal puesta) no avisa", async () => {
  fetchMock.mockResolvedValue(respuesta(401, { detail: "Incorrect email or password" }));
  await expect(login("ana@example.com", "mal")).rejects.toMatchObject({ status: 401 });
  expect(fetchMock.mock.calls[0][0]).toMatch(/\/api\/auth\/login$/);
  expect(avisos).toBe(0);
  expect(sesionCaducada()).toBe(false);
});

test("otros errores (403, 500) no son una sesión caducada", async () => {
  fetchMock.mockResolvedValueOnce(respuesta(403, { detail: "Sin permiso" }));
  await expect(apiFetch("/api/erp/webs/reglas")).rejects.toMatchObject({ status: 403 });
  fetchMock.mockResolvedValueOnce(respuesta(500, null));
  await expect(apiFetch("/api/erp/webs/estado")).rejects.toMatchObject({ status: 500 });
  expect(avisos).toBe(0);
});

test("las descargas también avisan (apiFetchBlob y apiDownloadBlob), con su error de siempre", async () => {
  fetchMock.mockResolvedValue(respuesta(401));
  await expect(apiFetchBlob("/api/emails/e-1/attachments/a-1")).rejects.toThrow("Could not validate credentials");
  expect(avisos).toBe(1);
  setStoredToken("tok-2");
  await expect(apiDownloadBlob("/api/erp/orders/o-1/factusol-pedido-pdf"))
    .rejects.toMatchObject({ name: "DownloadError", status: 401 });
  expect(avisos).toBe(2);
});

test("tras volver a entrar, el siguiente 401 vuelve a avisar", async () => {
  fetchMock.mockResolvedValueOnce(respuesta(401));
  await expect(apiFetch("/api/erp/webs/estado")).rejects.toBeInstanceOf(ApiError);
  expect(avisos).toBe(1);

  fetchMock.mockResolvedValueOnce(respuesta(200, { access_token: "nuevo", token_type: "bearer", requires_2fa: false }));
  await login("ana@example.com", "bien");
  expect(sesionCaducada()).toBe(false);

  fetchMock.mockResolvedValueOnce(respuesta(401));
  await expect(apiFetch("/api/erp/webs/estado")).rejects.toBeInstanceOf(ApiError);
  expect(avisos).toBe(2);
});
