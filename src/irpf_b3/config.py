from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    # Ollama / Classifier
    ollama_url: str = Field(default="http://localhost:11434/api/generate", description="URL for the Ollama API")
    ollama_model: str = Field(default="qwen2.5:7b", description="Model name for Ollama")
    
    # Global Output
    docs_output_dir: str = Field(default="docs/pdf", description="Directory where downloaded PDFs and text files are stored")
    
    # Shared / Global B3
    b3_max_workers: int = Field(default=15, description="Max thread workers for concurrent tasks")
    b3_api_retries: int = Field(default=3)
    b3_retry_sleep_seconds: float = Field(default=2.0)
    b3_default_page_size: int = Field(default=120)
    b3_language: str = Field(default="pt-br")
    b3_http_headers: dict = Field(default={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
    
    # Companies
    b3_initial_companies_url_template: str = Field(default="https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetInitialCompanies/{payload_b64}")
    b3_api_timeout: float = Field(default=15.0)
    companies_cache_filename: str = Field(default="companies.json")
    
    # Documents / CVM
    b3_material_facts_url_template: str = Field(default="https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall/GetMaterialFacts/{payload_b64}")
    b3_docs_timeout: float = Field(default=30.0)
    cvm_pdf_url: str = Field(default="https://www.rad.cvm.gov.br/ENET/frmExibirArquivoIPEExterno.aspx/ExibirPDF")
    cvm_pdf_institution_code: str = Field(default="2")
    cvm_http_headers: dict = Field(default={
        "Content-Type": "application/json; charset=utf-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    })
    cvm_pdf_timeout: float = Field(default=40.0)
    empty_years_threshold: int = Field(default=4)
    docs_start_year: int = Field(default=2018) # 2000

    # Document file extensions
    ext_pdf: str = Field(default="pdf", description="Extension for PDF documents")
    ext_doc: str = Field(default="doc", description="Extension for legacy OLE2 Word documents")
    ext_bin: str = Field(default="bin", description="Extension for unknown/binary documents")

    @property
    def supported_extensions(self) -> list[str]:
        """All supported download extensions, in detection-priority order."""
        return [self.ext_pdf, self.ext_doc, self.ext_bin]

    # Categories
    b3_allow_all_categories: bool = Field(default=True, description="Whether to allow all categories for exploratory debug")
    high_relevance_categories: set = Field(default={
        "assembleia",
        "aviso_aos_acionistas",
        "comunicado_ao_mercado",
        "fato_relevante",
        "reuniao_da_administracao",
        "valores_mobiliarios_negociados_e_detidos",
        "relatorio_proventos",
    })
    medium_relevance_categories: set = Field(default={
        "estatuto_social",
        "documentos_de_oferta_de_distribuicao_publica",
    })

    @property
    def allowed_categories(self) -> set:
        return self.high_relevance_categories | self.medium_relevance_categories

    # Corporate Event Scanning (Step 3)
    corporate_event_pattern_str: str = Field(
        default=r"bonifica|desdobrament|agrupament|subscri|split|inplit|fraç|frac",
        description="Regex stems for corporate event keyword matching"
    )
    corporate_event_context_chars: int = Field(
        default=500,
        description="Characters of context to extract around keyword matches"
    )
    llm_triage_timeout: float = Field(default=120.0)
    llm_analysis_timeout: float = Field(default=300.0)
    llm_max_text_length: int = Field(default=100_000)

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

settings = Settings()
