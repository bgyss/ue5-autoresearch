import unreal

level_subsystem = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
actor_subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)

factory = unreal.LevelSequenceFactoryNew()
seq = unreal.AssetToolsHelpers.get_asset_tools().create_asset(
    "DebugSeq", "/Game/Sequences", unreal.LevelSequence, factory
)

camera = actor_subsystem.spawn_actor_from_class(unreal.CineCameraActor, unreal.Vector(0, 0, 0))
binding = seq.add_possessable(camera)
track = binding.add_track(unreal.MovieScene3DTransformTrack)
section = track.add_section()

unreal.log("SECTION TYPE: {}".format(type(section)))
unreal.log("SECTION DIR: {}".format([m for m in dir(section) if "chan" in m.lower()]))
unreal.log("SECTION ALL: {}".format(dir(section)))
