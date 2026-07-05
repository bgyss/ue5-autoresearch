"""
Headless scene builder for the ue5-autoresearch benchmark project.

Run once (not part of the evaluate loop) via:
    harness/setup_scene.sh

Creates /Game/Maps/BenchmarkScene: a de-Nanited scene with a handful of
static-mesh props, dynamic lighting (Lumen software), a CineCameraActor, and
a LevelSequence that auto-plays a fixed flythrough on BeginPlay. This is the
one-time M0 substrate; the evaluate loop never touches this script.
"""
import traceback
import unreal

MAP_PATH = "/Game/Maps/BenchmarkScene"
SEQUENCE_PACKAGE = "/Game/Sequences"
SEQUENCE_NAME = "BenchmarkFlythrough"

level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
asset_registry = unreal.AssetRegistryHelpers.get_asset_registry()


def spawn(mesh_path, location, rotation=(0, 0, 0), scale=(1, 1, 1), name=None):
    # NOTE: unreal.EditorActorSubsystem.spawn_actor_from_object() routes through
    # UPlacementSubsystem, which assumes editor-UI placement-mode state that is
    # never initialized in a headless `-run=pythonscript` commandlet -> SIGSEGV
    # in UPlacementSubsystem::FindAssetFactoryFromAssetData. Spawn a plain
    # StaticMeshActor via spawn_actor_from_class instead and assign the mesh
    # directly to its component; this bypasses the placement subsystem.
    mesh = unreal.EditorAssetLibrary.load_asset(mesh_path)
    if mesh is None:
        raise RuntimeError(f"Failed to load mesh asset: {mesh_path}")
    actor = actor_subsystem.spawn_actor_from_class(
        unreal.StaticMeshActor,
        unreal.Vector(*location),
        unreal.Rotator(*rotation),
    )
    actor.static_mesh_component.set_static_mesh(mesh)
    actor.set_actor_scale3d(unreal.Vector(*scale))
    if name:
        actor.set_actor_label(name)
    return actor


def build_level():
    # Rebuild from scratch: loading the existing map and re-spawning would
    # duplicate every prop/light/camera on each rerun.
    if unreal.EditorAssetLibrary.does_asset_exist(MAP_PATH):
        unreal.EditorAssetLibrary.delete_asset(MAP_PATH)
    level_subsystem.new_level(MAP_PATH)

    # Ground plane, classic (non-Nanite) meshes with engine-default LODs.
    spawn("/Engine/BasicShapes/Plane.Plane", (0, 0, 0), scale=(50, 50, 1), name="Ground")

    props = [
        ("/Engine/BasicShapes/Cube.Cube", (300, 0, 50), (0, 0, 0), (1, 1, 1)),
        ("/Engine/BasicShapes/Sphere.Sphere", (0, 300, 75), (0, 0, 0), (1.5, 1.5, 1.5)),
        ("/Engine/BasicShapes/Cylinder.Cylinder", (-300, 0, 100), (0, 0, 0), (1, 1, 2)),
        ("/Engine/BasicShapes/Cone.Cone", (0, -300, 50), (0, 0, 0), (1, 1, 1)),
        ("/Engine/BasicShapes/Cube.Cube", (600, 600, 150), (0, 0, 45), (3, 3, 3)),
        ("/Engine/BasicShapes/Sphere.Sphere", (-600, 600, 200), (0, 0, 0), (4, 4, 4)),
    ]
    for i, (mesh, loc, rot, scale) in enumerate(props):
        spawn(mesh, loc, rot, scale, name=f"Prop_{i}")

    # Dynamic lighting only (bAllowStaticLighting=False project-wide): a
    # directional light + sky light drive Lumen GI/reflections.
    sun = actor_subsystem.spawn_actor_from_class(unreal.DirectionalLight, unreal.Vector(0, 0, 500))
    sun.set_actor_rotation(unreal.Rotator(-40, 30, 0), False)
    sun_comp = sun.get_component_by_class(unreal.DirectionalLightComponent)
    sun_comp.set_intensity(6.0)

    sky = actor_subsystem.spawn_actor_from_class(unreal.SkyLight, unreal.Vector(0, 0, 0))
    sky_comp = sky.get_component_by_class(unreal.SkyLightComponent)
    sky_comp.set_editor_property("real_time_capture", True)

    unreal.EditorLevelLibrary.spawn_actor_from_class(unreal.SkyAtmosphere, unreal.Vector(0, 0, 0)) \
        if hasattr(unreal, "SkyAtmosphere") else None

    camera = actor_subsystem.spawn_actor_from_class(
        unreal.CineCameraActor, unreal.Vector(800, -800, 250)
    )
    camera.set_actor_rotation(unreal.Rotator(-10, 135, 0), False)
    camera.set_actor_label("BenchmarkCamera")

    # PlayerStart at the camera's start pose: guarantees the default pawn's
    # view shows the scene even if the LevelSequence camera cut fails to take
    # over in -game. Without one, the pawn spawns at the world origin (inside
    # the ground plane) and every screenshot is identical black
    # ("FindPlayerStart: ... NO PLAYERSTART" in the -game log).
    start = actor_subsystem.spawn_actor_from_class(
        unreal.PlayerStart, unreal.Vector(800, -800, 250)
    )
    start.set_actor_rotation(unreal.Rotator(-10, 135, 0), False)
    start.set_actor_label("BenchmarkStart")

    level_subsystem.save_current_level()
    return camera


def build_sequence(camera):
    if not unreal.EditorAssetLibrary.does_directory_exist(SEQUENCE_PACKAGE):
        unreal.EditorAssetLibrary.make_directory(SEQUENCE_PACKAGE)

    seq_path = f"{SEQUENCE_PACKAGE}/{SEQUENCE_NAME}"
    # Always rebuild from scratch: re-running against an existing asset would
    # stack duplicate possessables/tracks on top of the old ones.
    if unreal.EditorAssetLibrary.does_asset_exist(seq_path):
        unreal.EditorAssetLibrary.delete_asset(seq_path)
    factory = unreal.LevelSequenceFactoryNew()
    sequence = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
        SEQUENCE_NAME, SEQUENCE_PACKAGE, unreal.LevelSequence, factory
    )

    fps = 30
    duration_seconds = 8
    sequence.set_playback_start(0)
    sequence.set_playback_end(fps * duration_seconds)
    sequence.set_display_rate(unreal.FrameRate(fps, 1))

    camera_binding = sequence.add_possessable(camera)

    transform_track = camera_binding.add_track(unreal.MovieScene3DTransformTrack)
    transform_section = transform_track.add_section()
    transform_section.set_range(0, fps * duration_seconds)

    channels = transform_section.get_all_channels()
    keyframe_times = [0, fps * duration_seconds]
    start_loc = (800, -800, 250)
    end_loc = (-200, 800, 350)
    start_rot = (-10, 135, 0)
    end_rot = (-15, 225, 0)

    # Key by channel index, not name: the transform-section channels are named
    # "Location.X" ... "Rotation.X/Y/Z" (not "Rotation.Roll/Pitch/Yaw"), so
    # name-substring matching on Roll/Yaw/Pitch silently keyed nothing.
    # Order: 0-2 Location XYZ, 3-5 Rotation XYZ (X=roll, Y=pitch, Z=yaw).
    # start_rot/end_rot are (pitch, yaw, roll).
    def key_channel(index, values):
        ch = channels[index]
        for t, v in zip(keyframe_times, values):
            ch.add_key(unreal.FrameNumber(t), float(v))

    key_channel(0, (start_loc[0], end_loc[0]))   # Location.X
    key_channel(1, (start_loc[1], end_loc[1]))   # Location.Y
    key_channel(2, (start_loc[2], end_loc[2]))   # Location.Z
    key_channel(3, (start_rot[2], end_rot[2]))   # Rotation.X = roll
    key_channel(4, (start_rot[0], end_rot[0]))   # Rotation.Y = pitch
    key_channel(5, (start_rot[1], end_rot[1]))   # Rotation.Z = yaw

    camera_cut_track = sequence.add_master_track(unreal.MovieSceneCameraCutTrack) \
        if hasattr(sequence, "add_master_track") else sequence.add_track(unreal.MovieSceneCameraCutTrack)
    camera_cut_section = camera_cut_track.add_section()
    camera_cut_section.set_range(0, fps * duration_seconds)
    binding_id = unreal.MovieSceneObjectBindingID()
    binding_id.set_editor_property("guid", camera_binding.get_id())
    camera_cut_section.set_camera_binding_id(binding_id)

    unreal.EditorAssetLibrary.save_asset(seq_path)
    return sequence


def place_sequence_actor(sequence):
    existing = [a for a in actor_subsystem.get_all_level_actors()
                if isinstance(a, unreal.LevelSequenceActor)]
    for a in existing:
        actor_subsystem.destroy_actor(a)

    seq_actor = actor_subsystem.spawn_actor_from_class(
        unreal.LevelSequenceActor, unreal.Vector(0, 0, 0)
    )
    seq_actor.set_actor_label("BenchmarkFlythrough_Player")
    seq_actor.set_sequence(sequence)

    playback_settings = seq_actor.playback_settings
    playback_settings.set_editor_property("play_rate", 1.0)
    playback_settings.set_editor_property("loop_count", unreal.MovieSceneSequenceLoopCount(0))
    playback_settings.set_editor_property("auto_play", True)
    seq_actor.set_editor_property("playback_settings", playback_settings)

    level_subsystem.save_current_level()


def main():
    camera = build_level()
    sequence = build_sequence(camera)
    place_sequence_actor(sequence)
    unreal.log("build_scene.py: BenchmarkScene + BenchmarkFlythrough created.")


try:
    main()
except Exception:
    err = traceback.format_exc()
    unreal.log_error(err)
    with open("/Users/briangyss/src/ue5-autoresearch/build_scene_error.log", "w") as f:
        f.write(err)
    raise
