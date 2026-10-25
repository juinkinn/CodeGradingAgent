from typing import Any

from deepeval.models import DeepEvalBaseLLM
from langchain_google_genai import ChatGoogleGenerativeAI
from langfuse.langchain import CallbackHandler
from pydantic import BaseModel

from app.env import settings


class GeminiJudge(DeepEvalBaseLLM):
    def __init__(self, model_name: str | None = None) -> None:
        if not settings.gemini_api_key:
            raise RuntimeError(
                "Set GEMINI_API_KEY (or GOOGLE_API_KEY) to run DeepEval."
            )
        self.model_name = model_name or settings.gemini_model
        super().__init__(model=self.model_name)

    def load_model(self) -> ChatGoogleGenerativeAI:
        return ChatGoogleGenerativeAI(
            model=self.model_name,
            google_api_key=settings.gemini_api_key,
            temperature=0,
            max_retries=2,
        )

    def generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> str | BaseModel:
        model = (
            self.model.with_structured_output(schema)
            if schema is not None
            else self.model
        )
        result = model.invoke(
            prompt,
            config={
                "callbacks": [CallbackHandler()],
                "run_name": "deepeval-gemini-judge",
            },
            **kwargs,
        )
        if isinstance(result, BaseModel):
            return result
        return str(result.content)

    async def a_generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
        **kwargs: Any,
    ) -> str | BaseModel:
        model = (
            self.model.with_structured_output(schema)
            if schema is not None
            else self.model
        )
        result = await model.ainvoke(
            prompt,
            config={
                "callbacks": [CallbackHandler()],
                "run_name": "deepeval-gemini-judge",
            },
            **kwargs,
        )
        if isinstance(result, BaseModel):
            return result
        return str(result.content)

    def get_model_name(self) -> str:
        return f"Gemini ({self.model_name})"
