import unittest

from app.services.proposal_intelligence.schemas import (
    BudgetPlan,
    DeliveryModel,
    MethodologyPlan,
    MethodologyPhase,
)


class DeliveryAgentTests(unittest.TestCase):
    def test_delivery_model_is_how_not_phases(self) -> None:
        model = DeliveryModel(type="Agile", cadence="2-week sprints", confidence=0.9)
        method = MethodologyPlan(
            phases=[MethodologyPhase(name="Discovery", activities=["kickoff"], governance="")],
            confidence=0.9,
        )
        self.assertNotIn("Discovery", (model.type or ""))
        self.assertEqual(method.phases[0].name, "Discovery")

    def test_pricing_strategy_vs_model(self) -> None:
        b = BudgetPlan(
            pricingStrategy="Compete aggressively",
            pricingModel="Fixed Fee",
            confidence=0.8,
        )
        self.assertNotEqual(b.pricing_strategy, b.pricing_model)


if __name__ == "__main__":
    unittest.main()
