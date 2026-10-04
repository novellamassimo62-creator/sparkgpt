import io
import os
import secrets
import threading
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError


ROOT = Path(__file__).resolve().parent
MODEL_ID = "segmind/tiny-sd"
MODEL_REVISION = "cad0bd7495fa6c4bcca01b19a723dc91627fe84f"
MODEL_DIR = ROOT / "models" / "tiny-sd"
MODEL_FILES = (
    "model_index.json",
    "scheduler/scheduler_config.json",
    "text_encoder/config.json",
    "text_encoder/pytorch_model.bin",
    "tokenizer/merges.txt",
    "tokenizer/vocab.json",
    "unet/config.json",
    "unet/diffusion_pytorch_model.bin",
    "vae/config.json",
    "vae/diffusion_pytorch_model.bin",
)
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SIZE = (512, 512)
INFERENCE_STEPS = 8
PIPELINE_LOCK = threading.Lock()
PIPELINE = None


class ImageGenerationError(Exception):
    pass


def generator_status():
    try:
        import diffusers
        import torch
        from huggingface_hub import snapshot_download
    except ImportError:
        return {"available": False, "ready": False, "model": MODEL_ID}

    ready = all(
        (MODEL_DIR / file_name).is_file() and (MODEL_DIR / file_name).stat().st_size > 0
        for file_name in MODEL_FILES
    )
    return {
        "available": True,
        "ready": ready,
        "model": MODEL_ID,
        "device": "CPU",
        "torchVersion": torch.__version__,
        "diffusersVersion": diffusers.__version__,
    }


def _load_pipeline():
    global PIPELINE
    if PIPELINE is not None:
        return PIPELINE

    from diffusers import DiffusionPipeline
    from huggingface_hub import snapshot_download
    import torch

    MODEL_DIR.parent.mkdir(parents=True, exist_ok=True)
    try:
        snapshot_download(
            repo_id=MODEL_ID,
            revision=MODEL_REVISION,
            local_dir=MODEL_DIR,
        )
    except Exception as error:
        print(f"Download di Tiny-SD non riuscito: {error}")
        raise ImageGenerationError(
            "Non riesco a scaricare Tiny-SD da Hugging Face. Controlla la connessione e riprova."
        ) from error

    if not all((MODEL_DIR / file_name).is_file() for file_name in MODEL_FILES):
        raise ImageGenerationError(
            "Il download di Tiny-SD è incompleto. Riprova quando la connessione è stabile."
        )

    torch.set_num_threads(min(4, max(1, os.cpu_count() or 1)))
    try:
        pipeline = DiffusionPipeline.from_pretrained(
            MODEL_DIR,
            torch_dtype=torch.float32,
            local_files_only=True,
        )
        pipeline.set_progress_bar_config(disable=True)
        pipeline.to("cpu")
        PIPELINE = pipeline
    except Exception as error:
        print(f"Caricamento locale di Tiny-SD non riuscito: {error}")
        raise ImageGenerationError(
            "Non riesco a caricare Tiny-SD sul PC. Verifica che ci siano memoria e spazio sufficienti."
        ) from error

    return PIPELINE


def _prepare_reference_image(image_bytes):
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise ImageGenerationError("La foto supera il limite di 8 MB.")
    if not image_bytes:
        raise ImageGenerationError("Il file della foto è vuoto.")

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.format not in {"PNG", "JPEG", "WEBP"}:
                raise ImageGenerationError("Carica una foto PNG, JPG o WebP.")
            if image.width * image.height > 40_000_000:
                raise ImageGenerationError("La foto è troppo grande. Ridimensionala e riprova.")
            image.load()
            prepared = ImageOps.contain(
                image.convert("RGB"),
                IMAGE_SIZE,
                method=Image.Resampling.LANCZOS,
            )
            width = max(64, prepared.width // 8 * 8)
            height = max(64, prepared.height // 8 * 8)
            return prepared.resize((width, height), Image.Resampling.LANCZOS)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ImageGenerationError("Il file non contiene una foto valida o leggibile.") from error


def generate_image(prompt, reference_image=None):
    if not prompt.strip():
        raise ImageGenerationError("Scrivi una descrizione per l'immagine.")
    if len(prompt) > 1000:
        raise ImageGenerationError("La descrizione è troppo lunga (massimo 1000 caratteri).")

    prepared_image = _prepare_reference_image(reference_image) if reference_image else None
    with PIPELINE_LOCK:
        pipeline = _load_pipeline()
        import torch

        generator = torch.Generator(device="cpu").manual_seed(secrets.randbits(32))
        try:
            with torch.inference_mode():
                if prepared_image is None:
                    result = pipeline(
                        prompt=prompt,
                        width=IMAGE_SIZE[0],
                        height=IMAGE_SIZE[1],
                        num_inference_steps=INFERENCE_STEPS,
                        guidance_scale=7.0,
                        generator=generator,
                    )
                else:
                    from diffusers import StableDiffusionImg2ImgPipeline

                    image_pipeline = StableDiffusionImg2ImgPipeline.from_pipe(pipeline)
                    result = image_pipeline(
                        prompt=prompt,
                        image=prepared_image,
                        strength=0.65,
                        num_inference_steps=INFERENCE_STEPS,
                        guidance_scale=7.0,
                        generator=generator,
                    )
        except Exception as error:
            print(f"Generazione Tiny-SD non riuscita: {error}")
            raise ImageGenerationError(
                "La generazione non è riuscita. Riprova con una descrizione più semplice."
            ) from error

    output = io.BytesIO()
    result.images[0].save(output, format="PNG", optimize=True)
    return output.getvalue()
