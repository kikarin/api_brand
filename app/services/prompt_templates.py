"""Hardcoded garment prompts untuk Lucy VTON — format Decart substitute/add.

Ref: https://docs.platform.decart.ai/models/realtime/vton-3.5-prompting
Prompt harus bilang *region* yang diganti (upper/lower/outfit/…) + deskripsi
item dari reference image. Jangan deskripsikan “foto produk” saja.
"""

from __future__ import annotations

PROMPT_TEMPLATES: dict[str, str] = {
    "top": (
        "Substitute the upper body garment with the top shown in the reference "
        "image. Preserve its exact color, fabric texture, neckline, sleeves, "
        "and design details. Do not change the lower body garment, footwear, "
        "or accessories."
    ),
    "bottom": (
        "Substitute the lower body garment with the pants shown in the reference "
        "image — full-length trousers, not a skirt or shorts. Preserve the exact "
        "color, cut, pockets, seams, and hem details from the reference. Do not "
        "change the upper body garment, footwear, or accessories."
    ),
    "outer": (
        "Substitute the upper body garment with the jacket or outerwear shown in "
        "the reference image. Preserve structure, zippers, collar, and fabric "
        "texture. Do not change the lower body garment or footwear."
    ),
    "dress": (
        "Substitute the full outfit with the one-piece dress shown in the "
        "reference image — from bodice through the hem, covering torso and legs "
        "as a single garment. Preserve its exact color, fabric texture, straps "
        "or sleeves, waist, skirt length, and tier or seam details. Do not keep "
        "a separate top and bottom underneath; remove conflicting garments. "
        "Do not change footwear or accessories unless they are part of the dress."
    ),
    "hat": (
        "Add the hat shown in the reference image to the person's head. Preserve "
        "its exact shape, color, and material. Do not change clothing on the body."
    ),
    "shoes": (
        "Substitute the footwear with the shoes shown in the reference image. "
        "Preserve colorway and sole design. Do not change clothing on the body."
    ),
    "other": (
        "Substitute the matching garment region with the item shown in the "
        "reference image. Preserve original design and colors; do not invent "
        "details or change unrelated garments."
    ),
}


def template_key_for_category(category: str) -> str:
    return category if category in PROMPT_TEMPLATES else "other"


def get_prompt(template_key: str) -> str:
    return PROMPT_TEMPLATES.get(template_key, PROMPT_TEMPLATES["other"])


def list_templates() -> list[dict[str, str]]:
    return [{"key": k, "prompt": v} for k, v in PROMPT_TEMPLATES.items()]
