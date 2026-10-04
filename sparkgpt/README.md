# sparkgpt

sparkgpt usa Ollama e il modello Qwen2.5 3B, eseguiti in locale sul PC. La chat non richiede chiavi API, account, crediti o invio dei messaggi a servizi AI cloud.

## Avvio su Windows

1. Installa Ollama da [ollama.com/download/windows](https://ollama.com/download/windows).
2. Avvia `avvia_sparkgpt.bat`: scarica automaticamente il modello `qwen2.5:3b` la prima volta e poi avvia il sito.
3. Apri `http://127.0.0.1:8000`.

L'inferenza viene eseguita sul tuo PC, quindi non ci sono costi per richiesta né quote cloud. La risposta appare progressivamente; il modello resta caricato per 30 minuti dopo l'uso e le risposte predefinite sono concise per ridurre l'attesa. Restano necessari il download iniziale e lo spazio disco; la velocità dipende dal computer. Ollama e il modello non richiedono una chiave API. Il modello è più piccolo dei grandi servizi cloud e potrebbe essere meno capace su richieste complesse.

Il tono della chat si adatta al contesto: serio ed empatico nelle situazioni delicate; più leggero e amichevole, con qualche emoji, nelle chiacchiere informali. L'assistente risponde in modo diretto e sicuro, senza inventare informazioni. Puoi inviare altri messaggi mentre sta rispondendo: verranno messi in coda e gestiti in ordine.

La modalità Voce legge ad alta voce le risposte usando la sintesi vocale del browser; puoi riascoltarle o interromperle. Il riconoscimento del microfono dipende dal browser e dai permessi concessi; alcuni browser possono elaborare l'audio tramite un proprio servizio online. Il testo trascritto viene poi inviato al modello locale.

In chat puoi allegare una foto PNG, JPG o WebP (fino a 8 MB) e chiedere all'assistente di descriverla, leggere il testo o rispondere a una domanda su ciò che mostra. Le immagini vengono ridimensionate sul PC prima dell'analisi locale con Ollama e il modello visivo `qwen2.5vl:3b`; il launcher lo scarica al primo avvio (circa 3,2 GB). L'inferenza sul processore può comunque richiedere tempo. La generazione artistica nella modalità **Immagini** continua a usare Tiny-SD.

## Generazione e caricamento immagini

La modalità **Immagini** usa il modello locale `segmind/tiny-sd` con PyTorch CPU. Al primo utilizzo scarica il modello (circa 2,1 GB) nella cartella `models/`; il download richiede Internet, ma generazione, caricamento delle foto e modelli restano sul PC e non usano crediti o API a pagamento. Sul PC con grafica integrata la generazione può essere lenta e il modello compatto può produrre risultati meno dettagliati dei generatori cloud.

Scrivi una descrizione per generare un'immagine da zero oppure premi **Carica una foto** per usare una PNG, JPG o WebP (massimo 8 MB) come riferimento e trasformarla secondo la descrizione. La foto viene ridimensionata sul PC e non viene conservata dal server. Puoi scaricare l'immagine PNG generata dal messaggio della chat. Al primo avvio potrebbero essere installati automaticamente PyTorch CPU e le librerie locali necessarie.

## Account sparkgpt

La schermata di accesso è obbligatoria: la chat si sblocca solo dopo l'accesso. L'autenticazione usa [Clerk](https://clerk.com/), che offre un piano gratuito per iniziare; gli account, gli accessi Google e le passkey sono gestiti dal provider. I messaggi della chat continuano a essere elaborati dal modello Ollama locale.

L'app di sviluppo Clerk collegata a sparkgpt è già configurata con Google, email/password e passkey. La registrazione richiede nome e cognome; lo username non è obbligatorio. Le opzioni di accesso e registrazione sono incorporate nella schermata locale per mantenere il flusso di sviluppo su `localhost`.

Per configurare un'altra istanza Clerk:

1. Crea un'applicazione dalla [dashboard](https://dashboard.clerk.com/) e apri **API keys**.
2. Abilita l'accesso e la registrazione con email e password, **Google** e **passkeys**. Nella sezione **User model**, rendi obbligatori nome e cognome. In sviluppo, Clerk può fornire credenziali Google condivise.
3. Copia `clerk-config.example.json` in `clerk-config.json` e sostituisci il valore di `publishable_key` con la chiave pubblica `pk_test_...`. Il file locale è escluso da Git.
4. Avvia `avvia_sparkgpt.bat` e apri `http://localhost:8000` (non l'indirizzo IP): il dominio `localhost` è necessario per creare passkey di sviluppo.

Il server verifica la firma e la scadenza della sessione Clerk prima di accettare richieste alla chat. Il nome completo associato all'account, ad esempio “Sara Rossi”, viene mostrato nel profilo. Dopo aver effettuato l'accesso, usa il pulsante **Passkey** nel profilo per registrare una passkey sul dispositivo; poi potrai accedere con **Usa passkey**. Non condividere né inserire chiavi segrete Clerk nel browser: sparkgpt usa solo la chiave pubblica.
