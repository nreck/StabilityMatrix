using Avalonia.Controls;
using Avalonia.Threading;
using Avalonia.VisualTree;
using NSubstitute;
using StabilityMatrix.Avalonia.Services;
using StabilityMatrix.Avalonia.ViewModels;
using StabilityMatrix.Avalonia.Views;
using StabilityMatrix.Core.Models.Settings;
using StabilityMatrix.Core.Services;

namespace StabilityMatrix.UITests;

[Collection("TempDir")]
public class RemoteLibraryViewTests : TestBase
{
    [AvaloniaTheory]
    [InlineData(true)]
    [InlineData(false)]
    public void RemoteViewsRenderInventoryAndBindActions(bool packages)
    {
        var settings = Substitute.For<ISettingsManager>();
        settings.Settings.Returns(new Settings());
        using var service = new RemoteLibraryService(settings);
        var vm = new RemoteLibraryViewModel(service, settings, Substitute.For<IInferenceClientManager>())
        {
            ServerLabel = "servivor · Server library",
            Status = "Connected to servivor",
            FreeSpace = "41 GB free · 62 model files · 4 packages",
        };
        vm.Packages.Add(new("one", "ComfyUI", "ComfyUI", "/server/ComfyUI", "Running (existing process)", true, false, 8188));
        vm.Models.Add(new("library", "StableDiffusion/example.safetensors", "example.safetensors", "StableDiffusion", 2147483648));
        vm.Roots.Add(new("downloads", "Model download drive", "/media/tt/storage/StabilityMatrix/Models"));
        vm.SelectedRoot = vm.Roots[0];
        vm.SelectedModel = vm.Models[0];
        Assert.Equal("downloads", vm.SelectedRoot.Id);
        vm.Jobs.Add(new("job", "download: example.safetensors", "Complete", "Saved model on server"));
        Control view = packages ? new RemotePackagesView() : new RemoteModelsView();
        view.DataContext = vm;
        var window = new Window { Content = view, Width = 1100, Height = packages ? 850 : 1100 };
        window.Show();
        if (!packages)
            view.GetVisualDescendants().OfType<Expander>().First(e => Equals(e.Header, "Download or upload a model to the server")).IsExpanded = true;
        Dispatcher.UIThread.RunJobs();
        var grids = view.GetVisualDescendants().OfType<DataGrid>().ToArray();
        Assert.Equal(2, grids.Length);
        Assert.NotNull(grids[0].ItemsSource);
        var labels = view.GetVisualDescendants().OfType<TextBlock>().Select(t => t.Text).ToArray();
        Assert.Contains("Connected to servivor", labels);
        Assert.Contains(packages ? "ComfyUI" : "example.safetensors", labels);
        if (!packages) Assert.Contains("/media/tt/storage/StabilityMatrix/Models", labels);
        var action = view.GetVisualDescendants().OfType<Button>().First(b =>
            Equals(b.Content, packages ? "Launch" : "Rename / move"));
        Assert.NotNull(action.Command);
        if (Environment.GetEnvironmentVariable("SM_UI_SCREENSHOT_DIR") is { } directory)
        {
            Directory.CreateDirectory(directory);
            using var frame = window.CaptureRenderedFrame();
            Assert.NotNull(frame);
            frame.Save(Path.Combine(directory, packages ? "remote-packages.png" : "remote-models.png"));
        }
        window.Close();
    }
}
