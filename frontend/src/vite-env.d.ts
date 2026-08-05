/// <reference types="vite/client" />

/** Отпечаток сборки, подставляется Vite через `define` (см. vite.config.ts).
 * Используется для cache-busting скриптов из /static/js/, которые
 * подключаются <script src> в обход бандлера и потому не имеют контентного
 * хэша в имени файла. */
declare const __BUILD_ID__: string
