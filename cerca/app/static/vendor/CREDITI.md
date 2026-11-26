# Crediti e licenze dell'aspetto di Cerca

## Codice incluso

- **GSAP 3.14.2** (`gsap.min.js`), © GreenSock. Licenza "Standard no-charge": https://gsap.com/standard-license.
  Serve alle animazioni di `movimento.js`; se non si carica, Cerca funziona lo stesso, senza movimento.

## Caratteri (cartella `fonts/`)

- **Atkinson Hyperlegible Next** e **Atkinson Hyperlegible Mono**, © Braille Institute of America.
- **Instrument Serif**, © The Instrument Serif Project Authors.

Tutti con la SIL Open Font License 1.1 (testo in `fonts/OFL.txt`).

## Foto degli utenti

- Le foto date agli utenti di casa (`foto_profili.py`) sono di **Unsplash** (licenza Unsplash, uso libero),
  scaricate da https://picsum.photos al momento di assegnarle. Non sono incluse nel codice.

## Metodo e idee

- **unslop**, di Damir Sokolovsky ([@heydamir](https://x.com/heydamir)), https://github.com/heydamir/unslop,
  licenza CC BY 4.0. Il nuovo aspetto di Cerca è stato fatto seguendo il suo metodo: direzione dal prodotto
  ("il salotto la sera"), neutri caldi, un solo accento, niente emoji, il movimento solo dove spiega qualcosa.
- **OpenMotion**, https://github.com/nexu-io/openmotion, licenza Apache 2.0. Il modo di animare
  (timeline GSAP, linee che si disegnano, titoli che compaiono da una maschera) viene dalle sue ricette.
  Nessun file di OpenMotion è copiato qui.

## Componenti rifatti senza React

Questi componenti sono stati riscritti in JavaScript semplice in `movimento.js` e `style.css`, partendo
dall'idea e dal comportamento degli originali. Entrambi i progetti hanno la licenza MIT, riportata sotto.

- **kokonutui** (https://github.com/kokonut-labs/kokonutui): tieni premuto per confermare (hold-button),
  campanella che suona (bell-button), schede con l'indicatore che scorre (smooth-tab), avvisi che passano da
  "in corso" a "fatto" (promise-toast), stato vuoto (empty-state), barre dei controlli (uptime-card).
- **bklit-ui** (https://github.com/bklit/bklit-ui): la lancetta del disco con righello e zona rossa (gauge),
  la linea della velocità che scorre (live-line-chart), la cifra che scorre verso il valore nuovo.

```
MIT License

Copyright (c) 2025 kokonutUI
Copyright (c) 2026 uixmat (bklit-ui)

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
