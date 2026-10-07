# GUI-4 PLAN full source inventory

Product implementation is frozen. Seven overlapping source scopes retain whole files without excerpts or compression. The orchestrator must integrate all independent findings; each scoped opinion alone is not whole-bundle acceptance.

Human decision: Academic active semester and explicit MCP search selector are independent. Existing legacy retrieval is unchanged by Academic edits.

Frozen design and implementation specification are included in full in the authority scope. Runtime and UI current sources are pre-implementation evidence, not proof the GUI-4 features exist. Existing GUI23 scope was accepted independently and its credential sources/pins remain frozen.

## authority

- docs/plans/local-settings-gui-4-worker-plan.md
- docs/plans/local-settings-web-gui.md
- docs/plans/local-settings-web-gui-interaction-mock.md
- university-learning-system-v1.2-design-frozen.md
- university-learning-system-v1.2-implementation-spec-frozen.md
- contracts/study-behavior.md
- docs/plans/local-settings-gui-1-worker-plan.md
- docs/plans/local-settings-gui-2-worker-plan.md
- docs/plans/local-settings-gui-3-worker-plan.md

## academic-config

- docs/plans/local-settings-gui-4-worker-plan.md
- docs/plans/local-settings-web-gui.md
- contracts/study-behavior.md
- src/uls/config/schema.py
- src/uls/config/loader.py
- src/uls/config/validation.py
- src/uls/config/intake.py
- src/uls/config/errors.py
- src/uls/config/mutation.py
- src/uls/config/credentials.py
- src/uls/config/_secure_file.py
- src/uls/settings/config_service.py
- src/uls/settings/canvas_service.py
- src/uls/settings/canvas_checks.py
- src/uls/settings/journal.py
- src/uls/domain/ids.py
- src/uls/domain/errors.py
- tests/contract/_settings_support.py
- tests/contract/test_settings_config.py
- tests/contract/test_settings_cas.py
- tests/contract/test_settings_canvas_service.py
- tests/contract/test_settings_provider_checks.py
- tests/unit/test_intake_registry.py
- tests/unit/test_config_validation.py
- tests/unit/test_semester_retrieval_config.py

## academic-provider

- docs/plans/local-settings-gui-4-worker-plan.md
- docs/plans/local-settings-web-gui.md
- contracts/study-behavior.md
- src/uls/settings/provider_checks.py
- src/uls/settings/credential_service.py
- src/uls/settings/credential_roles.py
- src/uls/settings/credential_stores.py
- src/uls/settings/credential_admission.py
- src/uls/adapters/notion/intake.py
- src/uls/adapters/drive/worker.py
- src/uls/intake/registry.py
- src/uls/intake/identity.py
- src/uls/runtime.py
- src/uls/config/schema.py
- src/uls/config/intake.py
- src/uls/config/credentials.py
- src/uls/settings/config_service.py
- src/uls/settings/canvas_service.py
- tests/contract/_settings_support.py
- tests/contract/test_settings_canvas_service.py
- tests/contract/test_settings_provider_checks.py

## settings-ui

- docs/plans/local-settings-gui-4-worker-plan.md
- docs/plans/local-settings-web-gui.md
- contracts/study-behavior.md
- src/uls/settings/security.py
- src/uls/settings/app.py
- src/uls/settings/status.py
- src/uls/settings/static/index.html
- src/uls/settings/static/styles.css
- src/uls/settings/static/app.js
- tests/contract/_settings_support.py
- tests/contract/settings_ui_harness.cjs
- tests/contract/test_settings_ui.py
- src/uls/config/schema.py
- src/uls/settings/config_service.py

## settings-composition

- docs/plans/local-settings-gui-4-worker-plan.md
- docs/plans/local-settings-web-gui.md
- contracts/study-behavior.md
- src/uls/settings/fake_mode.py
- src/uls/settings/__init__.py
- src/uls/settings/launcher.py
- src/uls/settings/composition.py
- src/uls/settings/demo.py
- src/uls/cli/main.py
- src/uls/runtime.py
- tests/contract/test_settings_http.py
- tests/contract/test_settings_config.py
- tests/contract/test_settings_composition.py
- tests/contract/test_settings_canvas_service.py
- tests/contract/test_settings_provider_checks.py
- tests/unit/test_settings_launcher.py
- src/uls/settings/journal.py
- src/uls/settings/app.py
- src/uls/settings/security.py
- src/uls/settings/status.py
- src/uls/settings/config_service.py
- src/uls/config/schema.py
- tests/contract/_settings_support.py

## runtime-intake

- docs/plans/local-settings-gui-4-worker-plan.md
- docs/plans/local-settings-web-gui.md
- contracts/study-behavior.md
- src/uls/intake/worker.py
- src/uls/intake/study_note_composition.py
- src/uls/intake/registry.py
- src/uls/intake/identity.py
- src/uls/study_notes/config.py
- src/uls/study_notes/handler.py
- tests/integration/test_intake_worker_preview.py
- tests/integration/test_study_note_lifecycle_wiring.py
- tests/integration/test_study_note_composition.py
- src/uls/config/schema.py
- src/uls/config/intake.py
- src/uls/runtime.py
- src/uls/orchestration/locks.py
- src/uls/orchestration/runner.py
- src/uls/config/credentials.py

## runtime-entrypoints

- docs/plans/local-settings-gui-4-worker-plan.md
- docs/plans/local-settings-web-gui.md
- contracts/study-behavior.md
- src/uls/cli/main.py
- src/uls/runtime.py
- src/uls/worker.py
- src/uls/orchestration/runner.py
- src/uls/orchestration/locks.py
- src/uls/config/schema.py
- src/uls/config/intake.py
- src/uls/config/credentials.py
- src/uls/config/loader.py
- src/uls/config/validation.py
- src/uls/mcp/transports/local.py
- src/uls/mcp/transports/remote.py
- src/uls/mcp/server.py
- src/uls/study_notes/mcp.py
- tests/contract/test_worker_cli.py
- tests/contract/test_mcp_runtime.py
- tests/unit/test_study_note_cli.py
- tests/unit/test_local_worker_lock.py
- tests/integration/test_native_runtime.py

## Snapshot hashes

- docs/plans/local-settings-gui-4-worker-plan.md: SHA-256 d69e3c8687d42ebe77fd6d265fbde07322c4c08112186c57886e0518fd830540; 37544 bytes
- docs/plans/local-settings-web-gui.md: SHA-256 7bf3ff6037e9c52fcc24c00f2b8061b40a2a1a6fc4fd79510994d115131efaa6; 48316 bytes
- docs/plans/local-settings-web-gui-interaction-mock.md: SHA-256 1b0d08dd877cc38bf2de07053a5fe203dce89bac0e23e58a5ef6c1c7fd72c845; 18391 bytes
- university-learning-system-v1.2-design-frozen.md: SHA-256 45499aa642a52d97b59995d4bd525be77b05cd7ea7944f62e7a41938336cd6fa; 48653 bytes
- university-learning-system-v1.2-implementation-spec-frozen.md: SHA-256 10ab4498946af4bbf4b3f0cd20772a2f965ea095c50ee26fc2f1682b44f0d11f; 71442 bytes
- contracts/study-behavior.md: SHA-256 987d09ec152f91e368e070c5ccbe961602a18da8b6113afd968ac965406aae1a; 5533 bytes
- docs/plans/local-settings-gui-1-worker-plan.md: SHA-256 2343b2b135df54e6b1c63742ee5b121c040a24149b0671f69d26bd0baba60ca1; 32121 bytes
- docs/plans/local-settings-gui-2-worker-plan.md: SHA-256 c4ce9348a8b0648b581fad97e1026b24827d405532559741e903441140a7472c; 44780 bytes
- docs/plans/local-settings-gui-3-worker-plan.md: SHA-256 43188e127167465dd487696c37cc5a5d3223175e2a447c816637b84a089711e0; 29465 bytes
- src/uls/config/schema.py: SHA-256 b830c337fe6b822ab4eb2644ece88d234457baa236a87719f4d10d810705cc43; 8618 bytes
- src/uls/config/loader.py: SHA-256 d05e52a046bded9ea0eb818cd87c0e1d35e085343cb81bf62cd444af239f93f4; 16013 bytes
- src/uls/config/validation.py: SHA-256 d090a2f7240e6c74b708b0d3a572c11d6a2c4154f691d11435842586614f7a3c; 22579 bytes
- src/uls/config/intake.py: SHA-256 ec5c299da3f7ff00c1b1282242d44f5b4a5601e58cafeb559da2e87fd34e7337; 9461 bytes
- src/uls/config/errors.py: SHA-256 41e41c1f16b5f702e430cade93258c9b37053468c870f2081199bf4005771277; 783 bytes
- src/uls/config/mutation.py: SHA-256 9a5a647baeb718b632e8e07de8101c2fe1bbad17c71596bc8be4218c3157a9f5; 5078 bytes
- src/uls/config/credentials.py: SHA-256 ecbe398f52b4edf4267b07e368ae4e1a3413e0aa577dee0eac2fc4ae8cfefa86; 20347 bytes
- src/uls/config/_secure_file.py: SHA-256 62e701126abce681a14311aed9d33e3861b04ecdd4006184b159229582cc7836; 33598 bytes
- src/uls/settings/config_service.py: SHA-256 15e9954eb6eb6cf7cc160e7fd321c2871dfac1ec777e6d1ced5a1282faa6b456; 20644 bytes
- src/uls/settings/canvas_service.py: SHA-256 22989a85c367ffe60a2b2314ce3dbfbe8110472d8893a74bfb2fb5c71eb3f495; 26241 bytes
- src/uls/settings/canvas_checks.py: SHA-256 1a8551b10ad2bd273d1889f2e13959ac7d6d973928b1919fc291e4e2e3a3c9d0; 24808 bytes
- src/uls/settings/journal.py: SHA-256 5994511cfa70eadbb4c5478504cd59e51f8ce76b4169bd8aa76efbbce04dfd08; 55339 bytes
- src/uls/domain/ids.py: SHA-256 d44c7d993a8409174a1f1598fe90d637d860f07f56c9f600c15296f58b859d37; 4561 bytes
- src/uls/domain/errors.py: SHA-256 109d4e51819be88fc99ed704899ec69cf1ce507765b8576aefd849f58d3fd92a; 5048 bytes
- tests/contract/_settings_support.py: SHA-256 b40b41e86de25f0e6a3298b38bbdcff69a781877536bad7d133067a44312b873; 5196 bytes
- tests/contract/test_settings_config.py: SHA-256 e29dcdeb31fc416410e5d3a9f0164ef8728beb782f8c4467e1d5b4d321f014a2; 12160 bytes
- tests/contract/test_settings_cas.py: SHA-256 2feda71bc7ac42e47ab78bf5fd8a33195907151d44633fdea351bb885d111295; 10202 bytes
- tests/contract/test_settings_canvas_service.py: SHA-256 e7d0819d1295bae53a343bb3875b6309db13f06fc637d2dde6c3eb0f4c34c533; 30861 bytes
- tests/contract/test_settings_provider_checks.py: SHA-256 bfdc141062eca33ed97ed76f1d1b23cefb7bc4bc56d987f7970e45595551eb9e; 35592 bytes
- tests/unit/test_intake_registry.py: SHA-256 f95e21483336a0b0dffd0f91fd8ed197b75a22cccd2d56400df0930f5bb37a07; 8543 bytes
- tests/unit/test_config_validation.py: SHA-256 f78edefe38e1b5bf4581725c89a0ff2622b689262fe705b1898ec94994f72a6b; 8582 bytes
- tests/unit/test_semester_retrieval_config.py: SHA-256 562e88a261bb0220732665394f84e5762f2ba38db98877bf6820a42415c15423; 4064 bytes
- src/uls/settings/provider_checks.py: SHA-256 6f001daeb292af029cf1c6732c08b7f189ceb0d9dcb8a69612d96ef90303466d; 17855 bytes
- src/uls/settings/credential_service.py: SHA-256 d70bf1b5fcb63cb7d4c2f774a675be1b1d617b46a60bb4f932d3a7c831c62852; 28058 bytes
- src/uls/settings/credential_roles.py: SHA-256 f9f3ea23807747cc42ad7c08a9947ac255942619660218695e0e84eb58d763c9; 4222 bytes
- src/uls/settings/credential_stores.py: SHA-256 6ec843bcf25ef2046d3a580180468e658f923a816760c89a55eda7c5c7b88896; 5608 bytes
- src/uls/settings/credential_admission.py: SHA-256 9b72fa7d3ff333f643c203f064025f2a5e5edfe9dfc27d6d53ee2a5c5e03da87; 29151 bytes
- src/uls/adapters/notion/intake.py: SHA-256 74062f85f6dd115a86d70b13bbb209c417ae3725845af5d3b06ea443fc315407; 42775 bytes
- src/uls/adapters/drive/worker.py: SHA-256 157dccadff17dcc0b65fa78fbe560506798a2bd96d8dd0bab7886d05bd9efa16; 30632 bytes
- src/uls/intake/registry.py: SHA-256 0f49da79eb587752cd139cc1c75a1479b96470c1c965467a189a5ce788362367; 4533 bytes
- src/uls/intake/identity.py: SHA-256 838e546d5e356d177d5076c3bff8c86785d11812cd6bb1e9fa9679c61c3215ae; 9940 bytes
- src/uls/runtime.py: SHA-256 ccc992e3c506c4fc980a5cfcdd52d0a5b85af38e0af7a24064c8f16701921136; 12574 bytes
- src/uls/settings/security.py: SHA-256 603b9f8f426a245daddfa2ddf141469c2d2fe9a5e8f8bf86ebfa70060f55eb40; 15490 bytes
- src/uls/settings/app.py: SHA-256 bb3b19c6069f15e414831c16dadc5f5fc9032a5e3cc5576ca08c99611af4e538; 19068 bytes
- src/uls/settings/status.py: SHA-256 30b750d663494e23d620b1b820f5c8afbe439433b9bb698b0b450f849c32971b; 4873 bytes
- src/uls/settings/static/index.html: SHA-256 95a26b1782daee128ef73cc29dc963762e65c00c824bd81cd262456a8f9462d6; 13033 bytes
- src/uls/settings/static/styles.css: SHA-256 94ba20f548da36e11697e5f834d9da6489cc19a3d4ccc071041a0eced5f93449; 5783 bytes
- src/uls/settings/static/app.js: SHA-256 f4ace7f13bf2dd28e63037f0f9588065c5f1d427b9f74b27a15c03dfc26dca56; 67514 bytes
- tests/contract/settings_ui_harness.cjs: SHA-256 0336f98fa1cf584dd58480d619b8953f464cd9978cdd9e8df8ceb4eaae57b001; 52110 bytes
- tests/contract/test_settings_ui.py: SHA-256 e8b7e67c7e473b743db468e7c756f63ea592de1691eb6dda9183a2158f6fc03f; 21037 bytes
- src/uls/settings/fake_mode.py: SHA-256 d27735d0a53cd09f54ecd914d15da3f4d83c9d497c0163e2e765eeaed078f462; 3359 bytes
- src/uls/settings/__init__.py: SHA-256 6d97db8d73fbc18b527dc778595a4b3023b589edf60e2abc5d0616f386dca38d; 182 bytes
- src/uls/settings/launcher.py: SHA-256 5b69f64e4a36704da44d6e4a265feba237c9a9d5e359ff6d970061d6e28d52d3; 23937 bytes
- src/uls/settings/composition.py: SHA-256 451835ba4a70bac795c054dff00e89efa9fae5b81546a90e1537fa0d9d51ab04; 3859 bytes
- src/uls/settings/demo.py: SHA-256 1e882e62c6fac7b61e3cf20f6232cffc5e1da95d5b886e7c51caa311e773293e; 950 bytes
- src/uls/cli/main.py: SHA-256 3b986688e72b6ebbd48b250fd41398f204087a38ff484266dc10737af90fb11d; 30875 bytes
- tests/contract/test_settings_http.py: SHA-256 c2ee44948d45634ed70200b6852ec555a148135d4bef813245ae7859f78d4a26; 15251 bytes
- tests/contract/test_settings_composition.py: SHA-256 680cabe47a208f7ad3e189cf04139f79a740a8a114009b2b6120868eed714af3; 19039 bytes
- tests/unit/test_settings_launcher.py: SHA-256 f2823ca6a2919920abb028b85e39b2afcf83d326d7a90f97dc2a4ba7c31923cc; 18868 bytes
- src/uls/intake/worker.py: SHA-256 fa95cbc4c720ffb5677350c03eec663ff703c4162d4a9f27c90e0769d884d07d; 131637 bytes
- src/uls/intake/study_note_composition.py: SHA-256 ea262fa984943d211dda6ed1320cf92b60de448a6ea330d597f566dd82e47ef7; 10613 bytes
- src/uls/study_notes/config.py: SHA-256 93c0b1e6107a7f99068c1bdd41d128cbf038704e7a84b1e4ba6f27f7e4dbba12; 1991 bytes
- src/uls/study_notes/handler.py: SHA-256 8ef4ee18f62b323d55eb11580f264a6d60cdea9b93bc4cf65a756c6761a31984; 29577 bytes
- tests/integration/test_intake_worker_preview.py: SHA-256 faf5fa7f2f3b9d5a83cd4160a7f07dbcf9ed09eac7f538f63161e2761891206e; 32386 bytes
- tests/integration/test_study_note_lifecycle_wiring.py: SHA-256 8abfb876dc2fea897e18cd0793de6efcc37254b66b4da8d900cc476e4b97daa5; 20050 bytes
- tests/integration/test_study_note_composition.py: SHA-256 27cae4042dbc7a20ca82bcfcdfca499b0d9ab4c18b3788898371f7b78a12ddd4; 9889 bytes
- src/uls/orchestration/locks.py: SHA-256 d11d2dbcb644eab2d794b89c86f8c50e3c5e706650295d744658c9acf29c152c; 15130 bytes
- src/uls/orchestration/runner.py: SHA-256 3f23aea172c69ca41edf2fc6b656c5953aabbcc12fdcf713f41098e4669954cb; 4176 bytes
- src/uls/worker.py: SHA-256 3c13828f8006b5c4ee01101e313c513c19f7fe70ca88dbce07bd44f28bc9c0f9; 15109 bytes
- src/uls/mcp/transports/local.py: SHA-256 5d374f85047f08330590b739c2e34f9378a121e980bb292bdc1e41dc9ff5b8ef; 487 bytes
- src/uls/mcp/transports/remote.py: SHA-256 1c1ef359a9830ef9c6a259ca28abdd56b21a10169ed82d2a46b5cb751e5159ef; 19499 bytes
- src/uls/mcp/server.py: SHA-256 e6e32e712b4af59719520d07ea840b64946548aab5dd49c311b3227a1c56de7c; 7018 bytes
- src/uls/study_notes/mcp.py: SHA-256 4280ad6d739714b154ba0e3eb17981f2bfa9ad4d301859002377a8c9bc2aac9f; 9912 bytes
- tests/contract/test_worker_cli.py: SHA-256 61a8e947d63522c5760172c6128173a0f7d570b68189a6ac80b8d7d25bdbeba5; 9726 bytes
- tests/contract/test_mcp_runtime.py: SHA-256 ea82e73347e26348e2e0854b7ac53177370fc22835043246e624913ddbdaa2d5; 38954 bytes
- tests/unit/test_study_note_cli.py: SHA-256 ce96d7fc9bf8a0553b5f92a99fc936479fe18a2d1ca18c4ad4674ad064477dca; 1950 bytes
- tests/unit/test_local_worker_lock.py: SHA-256 01903c96553b097a830c660a04f350e3a2e0e9a61167089923062e80d903e72c; 7144 bytes
- tests/integration/test_native_runtime.py: SHA-256 63f8d651a567fd16265eb7b8c2fe449e6899d8f4d8cb85c16ccb8decec5fe939; 17061 bytes
