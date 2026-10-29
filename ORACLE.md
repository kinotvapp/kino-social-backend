# Kino Social en Oracle Cloud — guía

Desde el 29 de octubre de 2026 el servidor de Kino Social vive en Oracle Cloud (plan gratis). El servidor de este PC
(WSL) quedó como copia vieja: **no vuelvas a correr `start-all.ps1`**. Si lo haces, la gente escribe en dos bases distintas.

## Qué hay allá

| | |
| --- | --- |
| Cuenta | Oracle Cloud, nivel gratis («Free Tier»), región de origen **US East (Ashburn)** |
| Máquina | `kino-social`, **VM.Standard.E2.1.Micro** (Always Free): 1 núcleo, 1 GB de RAM + 3 GB de swap, disco de 47 GB |
| Sistema | Oracle Linux 9 (usuario `opc`, con `sudo`) |
| IP pública | **129.158.250.81** (efímera: se mantiene mientras no borres la máquina) |
| Dirección del plugin | **https://129-158-250-81.sslip.io** (Kino Social 0.4.4 en adelante) |
| Dirección vieja | https://social.comparadorinternet.co (túnel de Cloudflare, para Kino Social 0.4.3 y anteriores) |
| Llave SSH | `C:\Users\User\.ssh\kino-social-oci` (privada, **no la compartas**) y `kino-social-oci.pub` |

`sslip.io` es un servicio gratis: `129-158-250-81.sslip.io` siempre apunta a la IP 129.158.250.81. No es un dominio nuestro.

Servicios (systemd), todos arrancan solos si la máquina se reinicia:

| Servicio | Qué hace |
| --- | --- |
| `kino-social-api` | la API (FastAPI/uvicorn) en `127.0.0.1:8787`, usuario `kino` |
| `caddy` | HTTPS para la dirección sslip.io; saca y renueva el certificado solo (Let's Encrypt) |
| `kino-social-tunnel` | túnel de Cloudflare de la dirección vieja |
| `postgresql-18` | la base de datos `kino_social` (solo escucha en la máquina) |
| `redis` | caché y límites de uso (solo escucha en la máquina, 64 MB) |

Carpetas en el servidor: el código en `/opt/kino-social/app-src`, sus secretos en `/opt/kino-social/app-src/data`
(`pg.env` y `secret.key`), el entorno de Python en `/opt/kino-social/venv`.

## El atajo: `scripts/oracle.sh`

Todo lo de todos los días se hace desde **Git Bash**, en la carpeta `C:\dev\kino-social-backend`:

```bash
bash scripts/oracle.sh status
```

| Comando | Para qué |
| --- | --- |
| `bash scripts/oracle.sh ssh` | entrar al servidor |
| `bash scripts/oracle.sh status` | ver si todo está prendido, memoria, disco y que las dos direcciones respondan |
| `bash scripts/oracle.sh logs api 100` | ver los últimos 100 renglones del registro (`api`, `caddy`, `tunnel`, `postgres`, `redis`) |
| `bash scripts/oracle.sh deploy` | subir el código nuevo y reiniciar la API |
| `bash scripts/oracle.sh restart api` | reiniciar un servicio (`api`, `caddy`, `tunnel` o `all`) |
| `bash scripts/oracle.sh backup` | bajar una copia de la base a `backups/` |
| `bash scripts/oracle.sh restore backups/archivo.dump` | **reemplazar** la base del servidor por una copia (pregunta antes) |
| `bash scripts/oracle.sh psql` | consola SQL de la base |
| `bash scripts/oracle.sh put archivo.txt` | subir un archivo a `/tmp/` del servidor |
| `bash scripts/oracle.sh get /ruta/archivo` | bajar un archivo del servidor |

## Conectarse a mano

```bash
ssh -i ~/.ssh/kino-social-oci opc@129.158.250.81
```

Desde otro computador: copia la llave privada `kino-social-oci` a ese equipo (por un medio privado), o agrega otra llave
pública al servidor con `echo "ssh-ed25519 AAAA... nombre" >> ~/.ssh/authorized_keys` estando conectado.

Comandos útiles ya adentro:

```bash
sudo systemctl status kino-social-api      # estado de la API
sudo journalctl -u kino-social-api -f      # registro en vivo (Ctrl+C para salir)
sudo systemctl restart kino-social-api     # reiniciar la API
free -m; df -h /                           # memoria y disco
```

## Actualizar el código del servidor

1. Cambia el código en `C:\dev\kino-social-backend\app\` y corre las pruebas aquí
   (`.\.venv\Scripts\python.exe -m pytest -q`).
2. Haz commit (cuenta **kinotvapp**).
3. `bash scripts/oracle.sh deploy`

`deploy` sube solo `app/` y `requirements.txt`, nunca `data/`, así que los secretos del servidor no se tocan. Instala
librerías nuevas si las hay y reinicia la API. Si la API no responde después, **vuelve sola a la versión anterior**.

Si cambias tablas: el servidor las crea o ajusta al arrancar, igual que en el PC. Antes de un cambio grande, haz un
`backup`.

## Subir datos

- **Contenido de la red** (publicaciones, comunidades, cuentas oficiales): los scripts de `seed_*.py` hablan con la API.
  Para usarlos contra Oracle, cambia su `base_url` a `https://129-158-250-81.sslip.io` y ábrele la sesión desde el servidor
  (o córrelos allá: `put` el script y luego `ssh`, con `/opt/kino-social/venv/bin/python`).
- **Una base completa**: `bash scripts/oracle.sh restore backups/archivo.dump` (reemplaza todo lo que haya allá).
- **SQL suelto**: `bash scripts/oracle.sh psql` y escribe la consulta.
- **Archivos**: `put` / `get`.

## Copias de seguridad

`bash scripts/oracle.sh backup` deja un archivo `.dump` en `backups/` (está en `.gitignore`). Hazla antes de cualquier cambio
grande y de vez en cuando. Lo que no se puede perder nunca: **`data/secret.key`** (sin ella se pierden todas las cuentas) y
`data/pg.env` (contraseñas). Las dos están aquí en `data/` y en el servidor; guárdalas también fuera de este PC.

## Si algo falla

| Síntoma | Qué hacer |
| --- | --- |
| La app no carga la red | `status`; si la API no está `active`, `logs api` y `restart api` |
| Error de certificado | `logs caddy`; revisa que los puertos 80 y 443 sigan abiertos (abajo) |
| La dirección vieja no responde | `restart tunnel` y `logs tunnel` |
| Poca memoria | `status`; la máquina tiene 1 GB: con mucha gente, toca pasar a una ARM más grande (abajo) |
| La máquina no responde | consola de Oracle ▸ Compute ▸ Instances ▸ kino-social ▸ **Reboot** |

## La consola de Oracle

- **Máquina**: Compute ▸ Instances ▸ `kino-social` (reiniciar, ver la IP, métricas).
- **Puertos abiertos**: Networking ▸ Virtual cloud networks ▸ `vcn-20261029-1805` ▸ Security ▸ Default Security List ▸
  Security rules. Hoy: 22 (SSH), 80 y 443 (HTTPS). Además, el firewall de la máquina (`firewall-cmd`) deja pasar http y https.
- **IP**: en la VNIC de la máquina ▸ IP administration. Es efímera: si borras la máquina, se pierde.

## Cosas a tener en cuenta

- **Gratis mientras no salgas de "Always Free"**: no cambies la máquina a otra forma ni agregues discos sin revisar que
  digan *Always Free-eligible*. Una alerta de presupuesto (Billing ▸ Budgets) en 1 USD avisa si algo empieza a cobrar.
- **Si la IP cambia** (por borrar y recrear la máquina): la nueva dirección será `NUEVA-IP-CON-GUIONES.sslip.io`. Hay que
  cambiar el Caddyfile (`/etc/caddy/Caddyfile`), el `BASE` de `src/api.js` y los `hosts` del plugin, y sacar otra versión
  firmada de Kino Social. Para evitarlo se puede convertir la IP en **reservada** (Networking ▸ IP management ▸ Reserved
  public IPs).
- **Pasar a una máquina ARM** (4 núcleos, 24 GB, también gratis) cuando Oracle tenga cupo en Ashburn: crear la instancia
  nueva, repetir la instalación (ver «Cómo se montó») y mover la base con `backup` + `restore`.
- **Apagar la dirección vieja** cuando nadie use Kino Social 0.4.3 o anterior:
  `ssh ... 'sudo systemctl disable --now kino-social-tunnel'`.

## Cómo se montó (por si hay que repetirlo)

En una máquina Oracle Linux 9 nueva:

1. Swap de 2 GB (`/swapfile`).
2. PostgreSQL 18 del repositorio PGDG (en `/etc/yum.repos.d/pgdg-redhat-all.repo` cambiar `rhel-$releasever_major.$releasever_minor`
   por `rhel-$releasever_major`), `initdb`, memoria baja (`shared_buffers 64MB`, `max_connections 30`), `scram-sha-256` en
   `pg_hba.conf`, rol `kino_social` con la contraseña de `pg.env`, bases `kino_social` y `kino_social_test`.
3. Redis 7 (`dnf module install redis:7`), `bind 127.0.0.1`, `requirepass` de `pg.env`, `maxmemory 64mb`.
4. Python 3.12, usuario `kino`, venv en `/opt/kino-social/venv`, `pip install -r requirements.txt`.
5. El código en `/opt/kino-social/app-src` y `data/pg.env` + `data/secret.key` (permisos 600).
6. Unidades systemd `kino-social-api`, `caddy` (binario oficial en `/usr/local/bin/caddy`, `Caddyfile` con la dirección
   sslip.io y `reverse_proxy 127.0.0.1:8787`) y `kino-social-tunnel` (cloudflared con las credenciales del túnel
   `d3dfd239-…`).
7. `firewall-cmd --permanent --add-service=http --add-service=https` y las reglas 80/443 en la Security List.
