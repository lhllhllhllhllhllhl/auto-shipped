import json
import unittest
from pathlib import Path

from auto_shipped.platforms.guanyi import resolve_guanyi_policy_modules


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_json(relative_path: str) -> dict:
    return json.loads((PROJECT_ROOT / relative_path).read_text(encoding="utf-8"))


def load_guanyi_rules(file_name: str) -> dict:
    return resolve_guanyi_policy_modules(
        load_json(f"config/platform_rules/guanyi/{file_name}"),
        PROJECT_ROOT / "config/platform_rules",
    )


class ArchitectureContractTests(unittest.TestCase):
    def test_parsed_order_keeps_platform_fields_outside_common_contract(self):
        schema = load_json("contracts/parsed_order.schema.json")
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")
        self.assertFalse(schema["additionalProperties"])

        common_properties = set(schema["properties"])
        platform_fields = {
            "platform_order_no",
            "store_name",
            "warehouse_name",
            "payment_method",
            "product_code",
            "spec_code",
        }
        self.assertTrue(common_properties.isdisjoint(platform_fields))

    def test_route_ids_are_unique_and_resolve_to_known_platform_profile(self):
        routing = load_json("config/routing/order_routes_v1.json")
        routes = routing["routes"]
        route_ids = [route["route_id"] for route in routes]
        self.assertEqual(len(route_ids), len(set(route_ids)))
        self.assertEqual(routing["routing_key"], ["source_profile_id", "order_type"])

        known_profiles = {
            load_json("config/platform_profiles/guanyi_order_import_v1.json")["profile_id"],
        }
        for route in routes:
            self.assertIn(route["target_profile_id"], known_profiles)

    def test_guanyi_profile_owns_single_custom_import_template(self):
        profile = load_json("config/platform_profiles/guanyi_order_import_v1.json")
        self.assertTrue(profile["enabled"])
        self.assertEqual(profile["operation"], "custom_order_import")
        self.assertEqual(profile["output"]["artifact_id"], "custom_order_import")
        self.assertEqual(len(profile["columns"]), 48)
        self.assertEqual(len(profile["columns"]), len(set(profile["columns"])))
        self.assertEqual(profile["one_row_per"], "order_item")
        self.assertEqual(profile["order_group_key"], "平台单号")
        identity = profile["automation_identity_policy"]
        self.assertEqual(identity["strategy"], "append_suffix")
        self.assertEqual(identity["suffix"], "A")
        self.assertTrue(identity["confirmed"])

    def test_product_catalog_is_a_system_resource(self):
        profile = load_json("config/catalog/current_product_catalog.json")
        self.assertEqual(profile["platform"], "guanyi")
        self.assertEqual(profile["status"], "configured")
        self.assertTrue(profile["path"].endswith(".csv"))

    def test_bailihui_is_a_source_system_that_routes_to_guanyi(self):
        integration = load_json("config/platform_profiles/bailihui_v1.json")
        connector = load_json("config/source_connectors/bailihui_backend_v1.json")
        routing = load_json("config/routing/order_routes_v1.json")

        self.assertTrue(integration["enabled"])
        self.assertEqual(integration["role"], "source_system")
        self.assertFalse(integration["enabled_as_order_import_target"])
        self.assertEqual(
            integration["capabilities"]["order_excel_import"]["status"],
            "not_applicable",
        )
        self.assertEqual(connector["output_contract"], "ParsedOrder@1.0")

        route = next(
            item
            for item in routing["routes"]
            if item["route_id"] == "bailihui_backend_to_guanyi_v1"
        )
        self.assertEqual(route["target_platform"], "guanyi")
        self.assertEqual(route["target_profile_id"], "guanyi_order_import_v1")

    def test_clarification_contract_requires_a_blocking_question(self):
        schema = load_json("contracts/clarification_request.schema.json")
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")
        self.assertTrue(schema["properties"]["blocking"]["const"])
        self.assertIn("question", schema["required"])
        self.assertIn("reason", schema["required"])

    def test_batch_conversion_contract_is_fail_closed(self):
        schema = load_json("contracts/batch_conversion_result.schema.json")
        self.assertEqual(
            set(schema["properties"]["status"]["enum"]),
            {"ready", "needs_input", "blocked"},
        )
        self.assertIn("sources", schema["required"])
        self.assertIn("upload_manifest", schema["required"])
        self.assertFalse(schema["additionalProperties"])

    def test_approved_route_has_business_rule_coverage_scope(self):
        routing = load_json("config/routing/order_routes_v1.json")
        catalog = load_json(
            "config/business_rules/shangyu_sop_rule_catalog_v1.json"
        )
        route = next(
            item
            for item in routing["routes"]
            if item["route_id"] == "tiantian_warehouse_to_guanyi_v2"
        )
        self.assertEqual(route["approval_status"], "approved_for_conversion_test")
        self.assertIn(route["business_rule_scope_id"], catalog["scopes"])
        self.assertEqual(
            catalog["scopes"][route["business_rule_scope_id"]]["route_id"],
            route["route_id"],
        )

    def test_tiantian_store_is_product_routed_not_source_default(self):
        raw = load_json("config/platform_rules/guanyi/tiantian_warehouse_v2.json")
        self.assertNotIn("store_assignment_policy", raw)
        self.assertEqual(
            raw["policy_module_refs"]["store_assignment_policy"]["module_id"],
            "store.corn_to_guangming_manyuanqi.v1",
        )
        rules = load_guanyi_rules("tiantian_warehouse_v2.json")
        self.assertNotIn("store", rules["defaults"])
        policy = rules["store_assignment_policy"]
        self.assertEqual(policy["strategy"], "all_items_same_store_by_product")
        self.assertTrue(policy["confirmed"])
        self.assertEqual(policy["on_unmatched"], "ask_user")
        self.assertEqual(policy["on_multiple_stores"], "ask_user")
        self.assertEqual(policy["rules"][0]["store"], "光明满元气")
        self.assertEqual(
            policy["rules"][0]["match"]["product_name_contains_all"],
            ["玉米"],
        )

    def test_tiantian_buyer_member_uses_explicit_policy(self):
        rules = load_guanyi_rules("tiantian_warehouse_v2.json")
        self.assertNotIn("buyer_member", rules["defaults"])
        policy = rules["buyer_member_policy"]
        self.assertEqual(policy["strategy"], "fixed")
        self.assertEqual(policy["value"], "张")
        self.assertTrue(policy["confirmed"])

    def test_sam_buyer_member_and_bundle_rules_are_explicit(self):
        rules = load_guanyi_rules("sam_order_v1.json")
        self.assertEqual(
            rules["field_output_policy"]["product_name"]["strategy"],
            "source_name_with_display_spec_for_standard_items",
        )
        buyer = rules["buyer_member_policy"]
        self.assertEqual(buyer["strategy"], "sam_product_type_date_group")
        self.assertEqual(buyer["platform_prefix"], "sam")
        self.assertEqual(buyer["group_number_field"], "sam_group_number")
        self.assertEqual(
            {item["type_code"] for item in buyer["product_type_rules"]},
            {"YM", "SH"},
        )

        expansion = rules["item_expansion_policy"]
        self.assertEqual(expansion["strategy"], "confirmed_source_item_expansions")
        bundle = expansion["rules"][0]
        self.assertEqual(bundle["match"]["source_product_code"], "276388650")
        self.assertEqual(bundle["match"]["source_spec"], "光9-JTBN1T1-EH")
        self.assertEqual(
            [
                (item["product_code"], item["spec_code"], item["quantity_multiplier"])
                for item in bundle["components"]
            ],
            [
                ("JTBN1E1-EH", "6974768564842", 1),
                ("GMBN1", "6974768564804", 1),
            ],
        )
        self.assertEqual(rules["defaults"]["unit_price"]["value"], 0)
        self.assertEqual(rules["defaults"]["payment_amount"]["value"], 0)
        self.assertEqual(rules["defaults"]["freight"]["value"], "")
        self.assertTrue(rules["defaults"]["freight"]["confirmed"])
        self.assertEqual(rules["defaults"]["payment_method"]["value"], "网银在线")
        self.assertTrue(rules["defaults"]["payment_method"]["confirmed"])
        self.assertEqual(rules["defaults"]["order_type"]["value"], "销售订单")
        self.assertTrue(rules["defaults"]["order_type"]["confirmed"])

    def test_ndd_bundle_and_platform_number_rules_are_explicit(self):
        rules = load_json("config/platform_rules/guanyi/ndd_order_v1.json")
        number = rules["platform_order_number"]
        self.assertEqual(number["strategy"], "source_order_no_with_affixes")
        self.assertEqual(number["prefix"], "NDD")
        self.assertEqual(number["suffix"], "")
        expansion = rules["item_expansion_policy"]["rules"][0]
        self.assertEqual(
            expansion["match"]["source_product_name"],
            "光明 福利套餐四",
        )
        self.assertEqual(
            [
                (item["product_code"], item["spec_code"], item["quantity_multiplier"])
                for item in expansion["components"]
            ],
            [
                ("JTW8E1", "6974768564811", 1),
                ("JTBN1E1-EH", "6974768564842", 1),
            ],
        )

    def test_tiantian_detection_separates_format_from_company_evidence(self):
        profile = load_json("config/source_profiles/tiantian_warehouse_v2.json")
        detection = profile["detection"]
        self.assertEqual(detection["header_order"], "any")
        self.assertEqual(detection["unknown_header_policy"], "warn")
        identity = detection["company_identity"]
        self.assertTrue(identity["required"])
        self.assertEqual(identity["on_missing"], "ask_user")
        self.assertEqual(identity["rules"][0]["header"], "渠道名称")

    def test_product_mapping_registry_is_source_scoped(self):
        mappings = load_json("config/catalog/external_sku_mappings.json")
        self.assertEqual(mappings["schema_version"], "2.0")
        self.assertEqual(mappings["mappings"], [])
        policy = mappings["source_policies"]["tiantian_warehouse_v2"]
        self.assertFalse(policy["allow_direct_catalog_identifier"])
        self.assertTrue(policy["allow_exact_catalog_name"])
        self.assertTrue(policy["require_identity_guard_for_blank_source_spec"])
        guard = policy["mapping_identity_guards"][0]
        self.assertEqual(guard["source_value"], "2026DFYUMI001")
        self.assertTrue(guard["allowed_product_names"])

    def test_package_semantics_are_separate_and_bare_sticks_are_forbidden(self):
        mappings = load_json("config/catalog/external_sku_mappings.json")
        self.assertNotIn(
            "semantic_catalog_match",
            mappings["source_policies"]["rongzhida_text_v1"],
        )
        registry = load_json("config/catalog/package_semantics_v1.json")
        self.assertEqual(registry["registry_id"], "package_semantics_v1")
        rzd_rule = registry["source_semantics"]["rongzhida_text_v1"]["rules"][0]
        self.assertIn("根", rzd_rule["source_spec_values"])
        self.assertEqual(rzd_rule["semantic_type"], "single_stick_packaged")
        self.assertEqual(rzd_rule["target_name_contains_all"], ["彩袋单棒装"])
        forbidden = {
            (item["product_code"], item["spec_code"])
            for item in registry["forbidden_shipping_products"]
        }
        self.assertEqual(
            forbidden,
            {
                ("HN-RB1", "HNLB40"),
                ("CN-RB1", "CNLB40"),
                ("BN-RB1", "BNLB40"),
            },
        )

    def test_business_rule_catalog_references_known_rules(self):
        schema = load_json("contracts/business_rule_catalog.schema.json")
        catalog = load_json(
            "config/business_rules/shangyu_sop_rule_catalog_v1.json"
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")
        known = set(catalog["rules"])
        for scope in catalog["scopes"].values():
            for rule_ids in scope["required_rules"].values():
                self.assertTrue(set(rule_ids).issubset(known))

    def test_all_approved_guanyi_routes_govern_white_label_library(self):
        routing = load_json("config/routing/order_routes_v1.json")
        catalog = load_json(
            "config/business_rules/shangyu_sop_rule_catalog_v1.json"
        )
        rule_profiles = {
            payload["profile_id"]: payload
            for path in (PROJECT_ROOT / "config/platform_rules/guanyi").glob("*.json")
            if (payload := json.loads(path.read_text(encoding="utf-8"))).get(
                "profile_id"
            )
        }
        approved = [
            route
            for route in routing["routes"]
            if route.get("approval_status") == "approved_for_conversion_test"
        ]
        self.assertTrue(approved)
        for route in approved:
            scope = catalog["scopes"][route["business_rule_scope_id"]]
            self.assertIn(
                "GY-DAILY-GOODS-WHITE-LABEL",
                scope["required_rules"]["conversion"],
            )
            coverage = rule_profiles[route["platform_rules_profile_id"]][
                "business_rule_coverage"
            ]["rules"]["GY-DAILY-GOODS-WHITE-LABEL"]
            self.assertEqual(coverage["status"], "implemented")
            self.assertEqual(
                coverage["implementation_ref"],
                "guanyi.seller_remark.white_label_sku_library",
            )

    def test_company_registry_indexes_modules_without_copying_business_rules(self):
        schema = load_json("contracts/company_registry.schema.json")
        registry = load_json("config/companies/company_registry_v1.json")
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")
        self.assertEqual(registry["registry_id"], "shangyu_company_registry_v1")

        tiantian = next(
            company
            for company in registry["companies"]
            if company["company_id"] == "tiantian"
        )
        self.assertEqual(tiantian["display_name"], "恬田")
        self.assertEqual(
            tiantian["source_profiles"][0]["source_profile_id"],
            "tiantian_warehouse_v2",
        )
        workflow = tiantian["workflows"][0]
        self.assertEqual(workflow["route_id"], "tiantian_warehouse_to_guanyi_v2")
        self.assertEqual(
            workflow["platform_rules_profile_id"],
            "guanyi_tiantian_warehouse_v2",
        )
        self.assertNotIn("logistics_policy", tiantian)
        self.assertNotIn("field_mapping", tiantian)

        sam = next(
            company
            for company in registry["companies"]
            if company["company_id"] == "sam"
        )
        self.assertEqual(sam["display_name"], "SAM")
        self.assertEqual(
            sam["source_profiles"][0]["source_profile_id"],
            "sam_order_v1",
        )
        self.assertEqual(
            sam["workflows"][0]["platform_rules_profile_id"],
            "guanyi_sam_order_v1",
        )

    def test_feishu_mapping_governance_has_owner_publisher_and_fail_closed_controls(self):
        config = load_json(
            "config/integrations/feishu_mapping_governance_v1.json"
        )
        route_schema = load_json(
            "contracts/feishu_pending_sheet_route.schema.json"
        )
        proposal_schema = load_json(
            "contracts/pending_product_mapping_proposal.schema.json"
        )

        self.assertEqual(
            config["status"],
            "shared_mapping_runtime_ready",
        )
        self.assertEqual(
            config["adapter_status"]["lark_cli_pending_gateway"],
            "ready",
        )
        self.assertEqual(
            config["adapter_status"]["official_mapping_snapshot"],
            "ready",
        )
        self.assertEqual(config["adapter_status"]["owner_publisher"], "ready")
        self.assertTrue(config["publisher_policy"]["allowed_user_ids"])
        self.assertEqual(
            config["publisher_policy"]["conflict_policy"],
            "fail_entire_batch",
        )
        self.assertTrue(config["publisher_policy"]["change_log_required"])
        self.assertEqual(
            config["official_repository"]["spreadsheet_token"],
            "QsLYsongYhoIdXtaL2GcB8Rinwe",
        )
        self.assertEqual(config["official_repository"]["mapping_sheet_id"], "13abc6")
        self.assertEqual(config["official_repository"]["metadata_sheet_id"], "CPVHDe")
        self.assertEqual(config["official_repository"]["publish_log_sheet_id"], "jl3rEi")
        self.assertEqual(
            config["pending_repository"]["spreadsheet_token"],
            "PH82sw3sBhbiJAtVPIScA96Yn3b",
        )
        self.assertEqual(config["pending_repository"]["route_sheet_id"], "U8k7Mz")
        self.assertEqual(config["pending_repository"]["template_sheet_id"], "eb2307")
        self.assertEqual(config["provider_priority"][0], "agent_native_feishu")
        self.assertTrue(config["identity_policy"]["reject_shared_bot_identity"])
        self.assertTrue(
            config["safety"]["pending_workbook_is_not_a_security_boundary"]
        )
        self.assertEqual(
            route_schema["properties"]["schema_version"]["const"],
            "1.0",
        )
        self.assertEqual(
            proposal_schema["properties"]["schema_version"]["const"],
            "1.0",
        )


if __name__ == "__main__":
    unittest.main()
