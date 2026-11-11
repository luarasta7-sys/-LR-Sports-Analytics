# LR Sports Analytics — versión de evaluación

Aplicación web móvil Python sin dependencias externas. **No es una herramienta de apuestas validada**.

- ESPN: partidos próximos y resultados de algunas ligas de fútbol y NBA.
- Ventana móvil de 48 horas y máximo 10 partidos por deporte.
- Solo probabilidades estimadas estrictamente superiores al 69 %.
- Fútbol: Poisson básico 1X2 y goles 0.5–5.5.
- NBA: ganador experimental. Tenis: sin predicciones hasta disponer de datos suficientes.
- No incluye aún 10 fuentes, 5 tipsters ni modelos calibrados.

## Ejecutar localmente

`python server.py` y abrir `http://localhost:8080`.

## Render

Repositorio con estos cuatro archivos en su raíz. Crear un Web Service Python gratuito con start command `python server.py`, o usar `render.yaml` como Blueprint.

No subir secretos ni claves al repositorio público. Las fuentes públicas pueden cambiar, fallar o limitar peticiones.
